import argparse
import logging
import os
import pickle
import time
from collections import Counter, defaultdict

from sqlalchemy import create_engine, func, inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from models.declarative import Article, ArticleCode
from utils import file_utils


DIR_DATA = os.environ.get(
    'DIR_DATA',
    '/app/data'
)

DIR_DICTIONARIES = os.path.join(
    DIR_DATA,
    'dictionaries'
)

STR_CONNECTION = os.environ.get('STR_CONNECTION')
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')
BATCH_SIZE = int(os.environ.get('ARTICLE_CODES_SYNC_BATCH_SIZE', '5000'))


def _load_article_codes(directory, version):
    filepath = file_utils.generate_file_path(directory, 'article-codes', version, '.data')
    with open(filepath, 'rb') as fin:
        return pickle.load(fin)


def _iter_article_code_rows(article_codes):
    for collection, items in article_codes.items():
        if collection == 'nbr':
            logging.warning('Ignorando registros article-codes da coleção nbr')
            continue

        for _, values in items.items():
            row = {
                'collection': values.get('collection') or collection,
                'pid_v2': values.get('pid_v2') or '',
                'pid_v3': values.get('pid_v3') or '',
                'doi': values.get('doi') or '',
            }

            if row['collection'] == 'nbr':
                row['collection'] = 'scl'

            if row['collection'] and (row['pid_v2'] or row['pid_v3']):
                yield row


def _build_article_map(session):
    article_map = {}
    for article_id, collection, pid in session.query(Article.id, Article.collection, Article.pid):
        article_map[(collection, pid)] = article_id
    return article_map


def _resolve_article_id(row, article_map):
    collection = row['collection']

    for pid in (row['pid_v3'], row['pid_v2']):
        if pid:
            article_id = article_map.get((collection, pid))
            if article_id:
                return article_id


def _code_pair(row):
    return (
        row.get('collection') or '',
        row.get('pid_v2') or '',
        row.get('pid_v3') or '',
    )


def _build_existing_code_maps(session, table_exists):
    if not table_exists:
        return {}, set(), {}

    duplicate_ids = {
        row.id
        for row in (
            session.query(ArticleCode.id)
            .group_by(ArticleCode.id)
            .having(func.count(ArticleCode.id) > 1)
        )
    }

    existing = {}
    existing_code_pairs = {}
    rows = session.query(
        ArticleCode.id,
        ArticleCode.collection,
        ArticleCode.pid_v2,
        ArticleCode.pid_v3,
        ArticleCode.doi,
    )
    for row in rows:
        if row.id not in existing:
            existing[row.id] = {
                'id': row.id,
                'collection': row.collection or '',
                'pid_v2': row.pid_v2 or '',
                'pid_v3': row.pid_v3 or '',
                'doi': row.doi or '',
            }

        existing_code_pairs[_code_pair(existing[row.id])] = row.id

    return existing, duplicate_ids, existing_code_pairs


def _merge_existing_with_new(existing, row):
    merged = {
        'id': row['id'],
        'collection': row['collection'],
        'pid_v2': existing.get('pid_v2', ''),
        'pid_v3': existing.get('pid_v3', ''),
        'doi': existing.get('doi', ''),
    }

    changed_fields = []
    for field in ('pid_v2', 'pid_v3', 'doi'):
        new_value = row.get(field) or ''
        old_value = existing.get(field, '')
        if new_value and old_value != new_value:
            merged[field] = new_value
            changed_fields.append(field)

    old_collection = existing.get('collection', '')
    if row['collection'] and old_collection != row['collection']:
        merged['collection'] = row['collection']
        changed_fields.append('collection')

    return merged, changed_fields


def _merge_pending_row(pending, row):
    changed_fields = []
    conflict_fields = []

    for field in ('collection', 'pid_v2', 'pid_v3', 'doi'):
        new_value = row.get(field) or ''
        old_value = pending.get(field) or ''

        if not new_value or old_value == new_value:
            continue

        if old_value:
            conflict_fields.append(field)
            continue

        pending[field] = new_value
        changed_fields.append(field)

    return changed_fields, conflict_fields


def _filter_conflicting_code_pairs(rows, existing_code_pairs, seen_code_pairs, stats):
    filtered_rows = []

    for row in rows:
        code_pair = _code_pair(row)
        article_id = row['id']

        existing_article_id = existing_code_pairs.get(code_pair)
        if existing_article_id and existing_article_id != article_id:
            stats['duplicate_existing_code_pairs'] += 1
            continue

        seen_article_id = seen_code_pairs.get(code_pair)
        if seen_article_id and seen_article_id != article_id:
            stats['duplicate_snapshot_code_pairs'] += 1
            continue

        seen_code_pairs[code_pair] = article_id
        filtered_rows.append(row)

    return filtered_rows


def _filter_rows_with_unique_code_pairs(rows_to_insert, rows_to_update, existing_code_pairs, stats):
    seen_code_pairs = {}
    rows_to_insert = _filter_conflicting_code_pairs(
        rows_to_insert,
        existing_code_pairs,
        seen_code_pairs,
        stats,
    )
    rows_to_update = _filter_conflicting_code_pairs(
        rows_to_update,
        existing_code_pairs,
        seen_code_pairs,
        stats,
    )
    return rows_to_insert, rows_to_update


def _chunk_rows(rows, chunk_size):
    for index in range(0, len(rows), chunk_size):
        yield rows[index:index + chunk_size]


def _detect_snapshot_conflicts(rows):
    conflicts = Counter()
    seen = defaultdict(set)

    for row in rows:
        for field in ('pid_v2', 'pid_v3', 'doi'):
            value = row.get(field)
            if value:
                seen[(field, row['collection'], value)].add(row['id'])

    for key, article_ids in seen.items():
        if len(article_ids) > 1:
            conflicts[key[0]] += 1

    return conflicts


def _prepare_rows(article_codes, article_map, existing, duplicate_ids, existing_code_pairs):
    stats = Counter()
    rows_to_insert_by_id = {}
    rows_to_update_by_id = {}
    rows_for_conflict_check = []

    for row in _iter_article_code_rows(article_codes):
        stats['candidates'] += 1
        article_id = _resolve_article_id(row, article_map)

        if not article_id:
            stats['orphans'] += 1
            continue

        row['id'] = article_id
        rows_for_conflict_check.append(row.copy())

        if article_id in duplicate_ids:
            stats['duplicate_existing_ids'] += 1
            continue

        existing_row = existing.get(article_id)
        if not existing_row:
            pending_row = rows_to_insert_by_id.get(article_id)
            if pending_row:
                stats['duplicate_snapshot_ids'] += 1
                _, conflict_fields = _merge_pending_row(pending_row, row)
                for field in conflict_fields:
                    stats[f'duplicate_snapshot_id_conflicts_{field}'] += 1
            else:
                rows_to_insert_by_id[article_id] = row
            continue

        merged, changed_fields = _merge_existing_with_new(existing_row, row)
        if changed_fields:
            pending_row = rows_to_update_by_id.get(article_id)
            if pending_row:
                stats['duplicate_snapshot_ids'] += 1
                _, conflict_fields = _merge_pending_row(pending_row, merged)
                for field in conflict_fields:
                    stats[f'duplicate_snapshot_id_conflicts_{field}'] += 1
            else:
                rows_to_update_by_id[article_id] = merged
        else:
            stats['unchanged'] += 1

    for field, count in _detect_snapshot_conflicts(rows_for_conflict_check).items():
        stats[f'conflicts_{field}'] = count

    rows_to_insert = list(rows_to_insert_by_id.values())
    rows_to_update = list(rows_to_update_by_id.values())
    rows_to_insert, rows_to_update = _filter_rows_with_unique_code_pairs(
        rows_to_insert,
        rows_to_update,
        existing_code_pairs,
        stats,
    )
    stats['inserts'] = len(rows_to_insert)
    stats['updates'] = len(rows_to_update)

    return rows_to_insert, rows_to_update, stats


def _apply_changes(session, rows_to_insert, rows_to_update, batch_size):
    for chunk in _chunk_rows(rows_to_insert, batch_size):
        try:
            session.bulk_insert_mappings(ArticleCode, chunk)
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            logging.exception('Erro ao inserir lote com %s registros em counter_article_code', len(chunk))
            raise

    for chunk in _chunk_rows(rows_to_update, batch_size):
        try:
            session.bulk_update_mappings(ArticleCode, chunk)
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            logging.exception('Erro ao atualizar lote com %s registros em counter_article_code', len(chunk))
            raise


def _log_collection_totals(rows):
    totals = Counter(row['collection'] for row in rows)
    for collection, total in sorted(totals.items()):
        logging.info('Candidatos em article-codes para %s: %s', collection, total)


def main():
    usage = 'Sincroniza o dicionário article-codes com a tabela counter_article_code.'
    parser = argparse.ArgumentParser(usage)

    parser.add_argument(
        '--dict_version_date',
        required=True,
        help='Data da versão do dicionário article-codes (YYYY-MM-DD)'
    )

    parser.add_argument(
        '-u', '--str_connection',
        default=STR_CONNECTION,
        help='String de conexão com banco de dados. Também pode ser informada por STR_CONNECTION.'
    )

    parser.add_argument(
        '--dry_run',
        action='store_true',
        help='Calcula inserts/updates/conflitos sem gravar no banco'
    )

    parser.add_argument(
        '--batch_size',
        type=int,
        default=BATCH_SIZE,
        help='Quantidade de registros gravados por lote'
    )

    parser.add_argument(
        '--logging_level',
        choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET'],
        dest='logging_level',
        default=LOGGING_LEVEL,
        help='Nível de log'
    )

    params = parser.parse_args()

    logging.basicConfig(
        level=params.logging_level,
        format='[%(asctime)s] %(levelname)s %(message)s',
        datefmt='%d/%b/%Y %H:%M:%S'
    )

    if not params.str_connection:
        logging.error('STR_CONNECTION não foi informado. Encerrando sem sincronizar counter_article_code.')
        return

    time_start = time.time()
    article_codes = _load_article_codes(DIR_DICTIONARIES, params.dict_version_date)
    candidate_rows = list(_iter_article_code_rows(article_codes))
    _log_collection_totals(candidate_rows)

    try:
        engine = create_engine(params.str_connection, pool_recycle=1800)
        table_exists = inspect(engine).has_table(ArticleCode.__tablename__)
        if not table_exists and not params.dry_run:
            ArticleCode.__table__.create(bind=engine, checkfirst=True)
            table_exists = True

        session_factory = sessionmaker(bind=engine)
        with session_factory() as session:
            article_map = _build_article_map(session)
            existing, duplicate_ids, existing_code_pairs = _build_existing_code_maps(session, table_exists)
            rows_to_insert, rows_to_update, stats = _prepare_rows(
                article_codes,
                article_map,
                existing,
                duplicate_ids,
                existing_code_pairs,
            )

            if params.dry_run:
                logging.info('Dry run ativo: nenhuma alteração será gravada')
            else:
                _apply_changes(session, rows_to_insert, rows_to_update, params.batch_size)
    except SQLAlchemyError:
        logging.exception('Erro de banco ao sincronizar counter_article_code. Verifique STR_CONNECTION e permissões.')
        return

    for key in sorted(stats):
        logging.info('%s: %s', key, stats[key])
    logging.info('Processamento concluído em %.2f segundos', time.time() - time_start)


if __name__ == '__main__':
    main()
