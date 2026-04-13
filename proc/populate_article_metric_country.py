import argparse
import logging
import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from models.declarative import ArticleMetricCountry


STR_CONNECTION = os.environ.get('STR_CONNECTION', 'mysql://user:pass@localhost:3306/matomo')
COLLECTION = os.environ.get('COLLECTION', 'scl')
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')
UNKNOWN_COUNTRY_CODE = os.environ.get('UNKNOWN_COUNTRY_CODE', 'ZZ').upper()
if len(UNKNOWN_COUNTRY_CODE) != 2:
    UNKNOWN_COUNTRY_CODE = 'ZZ'


def _create_table(engine):
    ArticleMetricCountry.__table__.create(bind=engine, checkfirst=True)


def _build_collection_filter(collection):
    if not collection:
        return '', {}
    return 'AND ca.collection = :collection', {'collection': collection}


def _count_source_rows(session, day, collection):
    collection_filter, params = _build_collection_filter(collection)
    params.update({'day': day})

    stmt = text(
        f'''
        SELECT COUNT(*)
        FROM counter_article_metric cam
        JOIN counter_article ca ON ca.id = cam.idarticle
        WHERE cam.year_month_day = :day
          {collection_filter}
        '''
    )
    return session.execute(stmt, params).scalar()


def _count_missing_country_map(session, day, collection):
    collection_filter, params = _build_collection_filter(collection)
    params.update({'day': day})

    stmt = text(
        f'''
        SELECT COUNT(*)
        FROM counter_article_metric cam
        JOIN counter_article ca ON ca.id = cam.idarticle
        LEFT JOIN counter_localization_country clc ON clc.idlocalization = cam.idlocalization
        WHERE cam.year_month_day = :day
          AND clc.idlocalization IS NULL
          {collection_filter}
        '''
    )
    return session.execute(stmt, params).scalar()


def _count_target_rows(session, day, collection):
    collection_filter, params = _build_collection_filter(collection)
    params.update({'day': day})

    stmt = text(
        f'''
        SELECT COUNT(*)
        FROM counter_article_metric_country camc
        WHERE camc.year_month_day = :day
          {collection_filter.replace('ca.collection', 'camc.collection')}
        '''
    )
    return session.execute(stmt, params).scalar()


def _populate_day(session, day, collection):
    collection_filter, params = _build_collection_filter(collection)
    params.update({
        'day': day,
        'unknown_country_code': UNKNOWN_COUNTRY_CODE,
    })

    stmt = text(
        f'''
        INSERT INTO counter_article_metric_country (
            collection,
            idarticle,
            idformat,
            idlanguage,
            year_month_day,
            country_code,
            total_item_requests,
            total_item_investigations,
            unique_item_requests,
            unique_item_investigations
        )
        SELECT
            ca.collection,
            cam.idarticle,
            cam.idformat,
            cam.idlanguage,
            cam.year_month_day,
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
        ON DUPLICATE KEY UPDATE
            total_item_requests = VALUES(total_item_requests),
            total_item_investigations = VALUES(total_item_investigations),
            unique_item_requests = VALUES(unique_item_requests),
            unique_item_investigations = VALUES(unique_item_investigations)
        '''
    )

    result = session.execute(stmt, params)
    session.commit()
    return result.rowcount


def main():
    usage = 'Cria e popula a tabela counter_article_metric_country para um único dia.'
    parser = argparse.ArgumentParser(usage)

    parser.add_argument(
        '-u', '--str_connection',
        default=STR_CONNECTION,
        help='String de conexão com banco de dados (mysql://username:password@host:port/database)'
    )

    parser.add_argument(
        '-d', '--date',
        required=True,
        help='Data a ser carregada no formato YYYY-MM-DD'
    )

    parser.add_argument(
        '-c', '--collection',
        default=COLLECTION,
        help='Acrônimo da coleção. Use vazio para processar todas as coleções'
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
    _create_table(engine)

    with session_factory() as session:
        source_rows = _count_source_rows(session, params.date, selected_collection)
        missing_country_rows = _count_missing_country_map(session, params.date, selected_collection)

    logging.info('Linhas de origem em counter_article_metric para %s: %s', params.date, source_rows)
    logging.info(
        'Linhas sem mapeamento em counter_localization_country para %s: %s',
        params.date,
        missing_country_rows,
    )

    time_start = time.time()
    with session_factory() as session:
        affected_rows = _populate_day(session, params.date, selected_collection)

    with session_factory() as session:
        target_rows = _count_target_rows(session, params.date, selected_collection)

    logging.info('Carga concluída em %.2f segundos', time.time() - time_start)
    logging.info('Rowcount retornado pelo INSERT/UPDATE: %s', affected_rows)
    logging.info('Linhas finais em counter_article_metric_country para %s: %s', params.date, target_rows)
    if missing_country_rows:
        logging.warning(
            'Foram encontradas localizações sem país mapeado. Essas linhas foram carregadas com country_code=%s',
            UNKNOWN_COUNTRY_CODE,
        )


if __name__ == '__main__':
    main()
