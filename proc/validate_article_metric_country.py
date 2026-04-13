import argparse
import logging
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


STR_CONNECTION = os.environ.get('STR_CONNECTION', 'mysql://user:pass@localhost:3306/matomo')
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')
UNKNOWN_COUNTRY_CODE = os.environ.get('UNKNOWN_COUNTRY_CODE', 'ZZ').upper()
if len(UNKNOWN_COUNTRY_CODE) != 2:
    UNKNOWN_COUNTRY_CODE = 'ZZ'


def _build_collection_filter(alias, collection):
    if not collection:
        return '', {}
    return f'AND {alias}.collection = :collection', {'collection': collection}


def _source_aggregation_subquery(collection_filter):
    return f'''
        SELECT
            ca.collection AS collection,
            cam.idarticle AS idarticle,
            cam.idformat AS idformat,
            cam.idlanguage AS idlanguage,
            cam.year_month_day AS year_month_day,
            COALESCE(clc.country_code, :unknown_country_code) AS country_code,
            SUM(cam.total_item_requests) AS total_item_requests,
            SUM(cam.total_item_investigations) AS total_item_investigations,
            SUM(cam.unique_item_requests) AS unique_item_requests,
            SUM(cam.unique_item_investigations) AS unique_item_investigations
        FROM counter_article_metric cam
        JOIN counter_article ca ON ca.id = cam.idarticle
        LEFT JOIN counter_localization_country clc ON clc.idlocalization = cam.idlocalization
        WHERE cam.year_month_day = :day
          {collection_filter}
        GROUP BY
            ca.collection,
            cam.idarticle,
            cam.idformat,
            cam.idlanguage,
            cam.year_month_day,
            COALESCE(clc.country_code, :unknown_country_code)
    '''


def _fetch_scalar(session, query, params):
    return session.execute(text(query), params).scalar()


def _fetch_all(session, query, params):
    return session.execute(text(query), params).fetchall()


def main():
    usage = 'Valida um dia da tabela counter_article_metric_country contra a agregação derivada da CAM.'
    parser = argparse.ArgumentParser(usage)

    parser.add_argument(
        '-u', '--str_connection',
        default=STR_CONNECTION,
        help='String de conexão com banco de dados (mysql://username:password@host:port/database)'
    )

    parser.add_argument(
        '-d', '--date',
        required=True,
        help='Data a ser validada no formato YYYY-MM-DD'
    )

    parser.add_argument(
        '-c', '--collection',
        default='',
        help='Acrônimo da coleção. Omitir este argumento valida todas as coleções'
    )

    parser.add_argument(
        '--sample_limit',
        type=int,
        default=10,
        help='Quantidade de divergências a listar na amostra'
    )

    parser.add_argument(
        '--logging_level',
        choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET'],
        dest='logging_level',
        default=LOGGING_LEVEL,
        help='Nível de log'
    )

    params = parser.parse_args()
    selected_collection = params.collection.strip() or None

    logging.basicConfig(
        level=params.logging_level,
        format='[%(asctime)s] %(levelname)s %(message)s',
        datefmt='%d/%b/%Y %H:%M:%S'
    )

    engine = create_engine(params.str_connection, pool_recycle=1800)
    session_factory = sessionmaker(bind=engine)

    source_collection_filter, source_collection_params = _build_collection_filter('ca', selected_collection)
    target_collection_filter, target_collection_params = _build_collection_filter('camc', selected_collection)
    params_common = {
        'day': params.date,
        'unknown_country_code': UNKNOWN_COUNTRY_CODE,
        **source_collection_params,
        **target_collection_params,
    }

    source_aggregation = _source_aggregation_subquery(source_collection_filter)

    source_rows_query = f'''
        SELECT COUNT(*)
        FROM counter_article_metric cam
        JOIN counter_article ca ON ca.id = cam.idarticle
        WHERE cam.year_month_day = :day
          {source_collection_filter}
    '''

    expected_rows_query = f'''
        SELECT COUNT(*)
        FROM (
            {source_aggregation}
        ) source_agg
    '''

    target_rows_query = f'''
        SELECT COUNT(*)
        FROM counter_article_metric_country camc
        WHERE camc.year_month_day = :day
          {target_collection_filter}
    '''

    source_sums_query = f'''
        SELECT
            COALESCE(SUM(source_agg.total_item_requests), 0) AS total_item_requests,
            COALESCE(SUM(source_agg.total_item_investigations), 0) AS total_item_investigations,
            COALESCE(SUM(source_agg.unique_item_requests), 0) AS unique_item_requests,
            COALESCE(SUM(source_agg.unique_item_investigations), 0) AS unique_item_investigations
        FROM (
            {source_aggregation}
        ) source_agg
    '''

    target_sums_query = f'''
        SELECT
            COALESCE(SUM(camc.total_item_requests), 0) AS total_item_requests,
            COALESCE(SUM(camc.total_item_investigations), 0) AS total_item_investigations,
            COALESCE(SUM(camc.unique_item_requests), 0) AS unique_item_requests,
            COALESCE(SUM(camc.unique_item_investigations), 0) AS unique_item_investigations
        FROM counter_article_metric_country camc
        WHERE camc.year_month_day = :day
          {target_collection_filter}
    '''

    mismatch_count_query = f'''
        SELECT COUNT(*)
        FROM (
            SELECT
                source_agg.collection,
                source_agg.idarticle,
                source_agg.idformat,
                source_agg.idlanguage,
                source_agg.year_month_day,
                source_agg.country_code
            FROM (
                {source_aggregation}
            ) source_agg
            LEFT JOIN counter_article_metric_country camc
                ON camc.collection = source_agg.collection
               AND camc.idarticle = source_agg.idarticle
               AND camc.idformat = source_agg.idformat
               AND camc.idlanguage = source_agg.idlanguage
               AND camc.year_month_day = source_agg.year_month_day
               AND camc.country_code = source_agg.country_code
            WHERE
                camc.id IS NULL OR
                camc.total_item_requests <> source_agg.total_item_requests OR
                camc.total_item_investigations <> source_agg.total_item_investigations OR
                camc.unique_item_requests <> source_agg.unique_item_requests OR
                camc.unique_item_investigations <> source_agg.unique_item_investigations

            UNION ALL

            SELECT
                camc.collection,
                camc.idarticle,
                camc.idformat,
                camc.idlanguage,
                camc.year_month_day,
                camc.country_code
            FROM counter_article_metric_country camc
            LEFT JOIN (
                {source_aggregation}
            ) source_agg
                ON source_agg.collection = camc.collection
               AND source_agg.idarticle = camc.idarticle
               AND source_agg.idformat = camc.idformat
               AND source_agg.idlanguage = camc.idlanguage
               AND source_agg.year_month_day = camc.year_month_day
               AND source_agg.country_code = camc.country_code
            WHERE camc.year_month_day = :day
              {target_collection_filter}
              AND source_agg.idarticle IS NULL
        ) mismatches
    '''

    mismatch_sample_query = f'''
        SELECT *
        FROM (
            SELECT
                'source_vs_target' AS mismatch_type,
                source_agg.collection AS collection,
                source_agg.idarticle AS idarticle,
                source_agg.idformat AS idformat,
                source_agg.idlanguage AS idlanguage,
                source_agg.year_month_day AS year_month_day,
                source_agg.country_code AS country_code,
                source_agg.total_item_requests AS source_total_item_requests,
                source_agg.total_item_investigations AS source_total_item_investigations,
                source_agg.unique_item_requests AS source_unique_item_requests,
                source_agg.unique_item_investigations AS source_unique_item_investigations,
                camc.total_item_requests AS target_total_item_requests,
                camc.total_item_investigations AS target_total_item_investigations,
                camc.unique_item_requests AS target_unique_item_requests,
                camc.unique_item_investigations AS target_unique_item_investigations
            FROM (
                {source_aggregation}
            ) source_agg
            LEFT JOIN counter_article_metric_country camc
                ON camc.collection = source_agg.collection
               AND camc.idarticle = source_agg.idarticle
               AND camc.idformat = source_agg.idformat
               AND camc.idlanguage = source_agg.idlanguage
               AND camc.year_month_day = source_agg.year_month_day
               AND camc.country_code = source_agg.country_code
            WHERE
                camc.id IS NULL OR
                camc.total_item_requests <> source_agg.total_item_requests OR
                camc.total_item_investigations <> source_agg.total_item_investigations OR
                camc.unique_item_requests <> source_agg.unique_item_requests OR
                camc.unique_item_investigations <> source_agg.unique_item_investigations

            UNION ALL

            SELECT
                'target_without_source' AS mismatch_type,
                camc.collection AS collection,
                camc.idarticle AS idarticle,
                camc.idformat AS idformat,
                camc.idlanguage AS idlanguage,
                camc.year_month_day AS year_month_day,
                camc.country_code AS country_code,
                NULL AS source_total_item_requests,
                NULL AS source_total_item_investigations,
                NULL AS source_unique_item_requests,
                NULL AS source_unique_item_investigations,
                camc.total_item_requests AS target_total_item_requests,
                camc.total_item_investigations AS target_total_item_investigations,
                camc.unique_item_requests AS target_unique_item_requests,
                camc.unique_item_investigations AS target_unique_item_investigations
            FROM counter_article_metric_country camc
            LEFT JOIN (
                {source_aggregation}
            ) source_agg
                ON source_agg.collection = camc.collection
               AND source_agg.idarticle = camc.idarticle
               AND source_agg.idformat = camc.idformat
               AND source_agg.idlanguage = camc.idlanguage
               AND source_agg.year_month_day = camc.year_month_day
               AND source_agg.country_code = camc.country_code
            WHERE camc.year_month_day = :day
              {target_collection_filter}
              AND source_agg.idarticle IS NULL
        ) mismatch_sample
        ORDER BY mismatch_type, collection, idarticle, idformat, idlanguage, country_code
        LIMIT :sample_limit
    '''

    with session_factory() as session:
        source_rows = _fetch_scalar(session, source_rows_query, params_common)
        expected_rows = _fetch_scalar(session, expected_rows_query, params_common)
        target_rows = _fetch_scalar(session, target_rows_query, params_common)
        source_sums = _fetch_all(session, source_sums_query, params_common)[0]
        target_sums = _fetch_all(session, target_sums_query, params_common)[0]
        mismatch_count = _fetch_scalar(session, mismatch_count_query, params_common)
        mismatch_sample = _fetch_all(
            session,
            mismatch_sample_query,
            {**params_common, 'sample_limit': params.sample_limit},
        )

    logging.info('Data validada: %s', params.date)
    logging.info('Coleção considerada: %s', selected_collection or 'todas')
    logging.info('Linhas brutas na CAM: %s', source_rows)
    logging.info('Linhas esperadas após agregação por país: %s', expected_rows)
    logging.info('Linhas carregadas em counter_article_metric_country: %s', target_rows)
    logging.info(
        'Somas origem: tir=%s tii=%s uir=%s uii=%s',
        source_sums.total_item_requests,
        source_sums.total_item_investigations,
        source_sums.unique_item_requests,
        source_sums.unique_item_investigations,
    )
    logging.info(
        'Somas destino: tir=%s tii=%s uir=%s uii=%s',
        target_sums.total_item_requests,
        target_sums.total_item_investigations,
        target_sums.unique_item_requests,
        target_sums.unique_item_investigations,
    )
    logging.info('Quantidade de chaves divergentes: %s', mismatch_count)

    if mismatch_sample:
        logging.warning('Amostra de divergências:')
        for row in mismatch_sample:
            logging.warning(dict(row._mapping))
    else:
        logging.info('Nenhuma divergência encontrada na amostra nem na contagem consolidada.')


if __name__ == '__main__':
    main()
