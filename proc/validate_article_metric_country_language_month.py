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


def _get_month_r5_metric_files(dir_r5_metrics, year_month):
    files = []
    for file_name in sorted([f for f in os.listdir(dir_r5_metrics) if 'r5-metrics' in f]):
        if get_date_from_file_path(file_name)[:7] == year_month:
            files.append(os.path.join(dir_r5_metrics, file_name))
    if not files:
        raise FileNotFoundError(f'Não foram encontrados arquivos r5_metrics para {year_month} em {dir_r5_metrics}')
    return files


def _load_maps(session):
    return {
        'pid': mount_pid_map(session),
        'language': mount_language_map(session),
        'format': mount_format_map(session),
        'localization': mount_localization_map(session),
        'localization_country': mount_localization_country_map(session),
        'issn': mount_issn_map(session),
    }


def _load_source_aggregation(session, year_month, collection, dir_r5_metrics):
    maps = _load_maps(session)
    aggregated = {}

    for metrics_file in _get_month_r5_metric_files(dir_r5_metrics, year_month):
        r5_metrics = read_r5_metrics(metrics_file)
        day_aggregated = _aggregate_by_keylist(
            r5_metrics,
            ['collection', 'idarticle', 'idlanguage', 'country_code', 'year_month'],
            maps,
        )

        for key, values in day_aggregated.items():
            if collection and key[0] != collection:
                continue
            if key not in aggregated:
                aggregated[key] = [0, 0, 0, 0]
            for idx, value in enumerate(values):
                aggregated[key][idx] += value

    return aggregated


def _load_target_rows(session, year_month, collection):
    params = {'year_month': year_month}
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
                idlanguage,
                country_code,
                year_month,
                total_item_investigations,
                total_item_requests,
                unique_item_investigations,
                unique_item_requests
            FROM counter_article_metric_country_language_month
            WHERE year_month = :year_month
              {collection_filter}
            '''
        ),
        params,
    ).fetchall()

    return {
        (row.collection, row.idarticle, row.idlanguage, row.country_code, row.year_month): [
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
    parser = argparse.ArgumentParser(
        'Valida um mês da tabela counter_article_metric_country_language_month contra os arquivos r5_metrics.'
    )

    parser.add_argument('-u', '--str_connection', default=STR_CONNECTION)
    parser.add_argument('-m', '--year_month', required=True, help='Mês no formato YYYY-MM')
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
        source_rows = _load_source_aggregation(session, params.year_month, selected_collection, params.dir_r5_metrics)
        target_rows = _load_target_rows(session, params.year_month, selected_collection)

    mismatches = []
    for key in sorted(set(source_rows.keys()) | set(target_rows.keys())):
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

    logging.info('Mês validado: %s', params.year_month)
    logging.info('Coleção considerada: %s', selected_collection or 'todas')
    logging.info('Linhas esperadas após agregação por país e idioma: %s', len(source_rows))
    logging.info('Linhas carregadas em counter_article_metric_country_language_month: %s', len(target_rows))
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
