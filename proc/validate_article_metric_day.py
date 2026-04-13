import argparse
import logging
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from proc.calculate_metrics import get_date_from_file_path
from proc.export_to_database import (
    DIR_R5_METRICS,
    _aggregate_by_keylist,
    mount_format_map,
    mount_issn_map,
    mount_language_map,
    mount_localization_country_map,
    mount_localization_map,
    mount_pid_map,
    read_r5_metrics,
)


STR_CONNECTION = os.environ.get('STR_CONNECTION', 'mysql://user:pass@localhost:3306/matomo')
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')


def _get_r5_metrics_file(dir_r5_metrics, day):
    for file_name in sorted([f for f in os.listdir(dir_r5_metrics) if 'r5-metrics' in f]):
        if get_date_from_file_path(file_name) == day:
            return os.path.join(dir_r5_metrics, file_name)
    raise FileNotFoundError(f'Não foi encontrado arquivo r5_metrics para {day} em {dir_r5_metrics}')


def _filter_collection(rows, collection):
    if not collection:
        return rows
    return [row for row in rows if row.collection == collection]


def _load_source_aggregation(session, day, collection, dir_r5_metrics):
    metrics_file = _get_r5_metrics_file(dir_r5_metrics, day)
    r5_metrics = read_r5_metrics(metrics_file)

    maps = {
        'pid': mount_pid_map(session),
        'language': mount_language_map(session),
        'format': mount_format_map(session),
        'localization': mount_localization_map(session),
        'localization_country': mount_localization_country_map(session),
        'issn': mount_issn_map(session),
    }

    aggregated = _aggregate_by_keylist(
        r5_metrics,
        ['collection', 'idarticle', 'year_month_day'],
        maps,
    )

    if collection:
        aggregated = {
            key: value
            for key, value in aggregated.items()
            if key[0] == collection
        }

    return aggregated


def _load_target_rows(session, day, collection):
    params = {'day': day}
    collection_filter = ''
    if collection:
        collection_filter = 'AND collection = :collection'
        params['collection'] = collection

    rows = session.execute(
        text(
            f'''
            SELECT
                collection,
                idarticle,
                year_month_day,
                total_item_investigations,
                total_item_requests,
                unique_item_investigations,
                unique_item_requests
            FROM counter_article_metric_day
            WHERE year_month_day = :day
              {collection_filter}
            '''
        ),
        params,
    ).fetchall()

    return {
        (row.collection, row.idarticle, str(row.year_month_day)): [
            row.total_item_investigations,
            row.total_item_requests,
            row.unique_item_investigations,
            row.unique_item_requests,
        ]
        for row in rows
    }


def _sum_metrics(rows_dict):
    totals = [0, 0, 0, 0]
    for values in rows_dict.values():
        for idx, value in enumerate(values):
            totals[idx] += value
    return totals


def main():
    parser = argparse.ArgumentParser('Valida um dia da tabela counter_article_metric_day contra o arquivo r5_metrics.')

    parser.add_argument('-u', '--str_connection', default=STR_CONNECTION)
    parser.add_argument('-d', '--date', required=True, help='Data no formato YYYY-MM-DD')
    parser.add_argument('-c', '--collection', default='', help='Acrônimo da coleção. Omitir valida todas.')
    parser.add_argument('--dir_r5_metrics', default=DIR_R5_METRICS)
    parser.add_argument('--sample_limit', type=int, default=10)
    parser.add_argument(
        '--logging_level',
        choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET'],
        default=LOGGING_LEVEL,
    )

    params = parser.parse_args()
    selected_collection = params.collection.strip() or None

    logging.basicConfig(
        level=params.logging_level,
        format='[%(asctime)s] %(levelname)s %(message)s',
        datefmt='%d/%b/%Y %H:%M:%S',
    )

    engine = create_engine(params.str_connection, pool_recycle=1800)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        source_rows = _load_source_aggregation(session, params.date, selected_collection, params.dir_r5_metrics)
        target_rows = _load_target_rows(session, params.date, selected_collection)

    source_keys = set(source_rows.keys())
    target_keys = set(target_rows.keys())
    mismatches = []

    for key in sorted(source_keys | target_keys):
        source_values = source_rows.get(key)
        target_values = target_rows.get(key)
        if source_values != target_values:
            mismatches.append({
                'key': key,
                'source': source_values,
                'target': target_values,
            })

    source_sums = _sum_metrics(source_rows)
    target_sums = _sum_metrics(target_rows)

    logging.info('Data validada: %s', params.date)
    logging.info('Coleção considerada: %s', selected_collection or 'todas')
    logging.info('Linhas esperadas após remoção de idioma/país/formato: %s', len(source_rows))
    logging.info('Linhas carregadas em counter_article_metric_day: %s', len(target_rows))
    logging.info(
        'Somas origem: tii=%s tir=%s uii=%s uir=%s',
        source_sums[0], source_sums[1], source_sums[2], source_sums[3],
    )
    logging.info(
        'Somas destino: tii=%s tir=%s uii=%s uir=%s',
        target_sums[0], target_sums[1], target_sums[2], target_sums[3],
    )
    logging.info('Quantidade de chaves divergentes: %s', len(mismatches))

    if mismatches:
        logging.warning('Amostra de divergências:')
        for row in mismatches[:params.sample_limit]:
            logging.warning(row)
    else:
        logging.info('Nenhuma divergência encontrada na amostra nem na contagem consolidada.')


if __name__ == '__main__':
    main()
