import argparse
import logging
import os
import time

from datetime import datetime, timedelta
from libs import lib_database, lib_status
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine.cursor import LegacyCursorResult
from models.declarative import (
    AggrArticleJournalYearMonthMetric,
    AggrArticleLanguageYearMonthMetric,
    AggrJournalLanguageYearMonthMetric,
    AggrJournalGeolocationYearMonthMetric,
    AggrJournalLanguageYOPYearMonthMetric,
    AggrJournalGeolocationYOPYearMonthMetric,
)
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
COLLECTION = os.environ.get('COLLECTION', 'scl')
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')
ENGINE = create_engine(STR_CONNECTION, pool_recycle=1800)
SESSION_FACTORY = sessionmaker(bind=ENGINE)
SESSION_BULK_LIMIT = int(os.environ.get('SESSION_BULK_LIMIT', '500'))
METRIC_COLUMNS = [
    'total_item_investigations',
    'total_item_requests',
    'unique_item_investigations',
    'unique_item_requests',
]

TABLES_TO_UPDATE_DEFAULT = [
    'aggr_article_journal_year_month_metric',
    'aggr_article_language_year_month_metric',
    'aggr_journal_language_year_month_metric',
    'aggr_journal_geolocation_year_month_metric',
    'aggr_journal_language_yop_year_month_metric',
    'aggr_journal_geolocation_yop_year_month_metric',
]


def _extract_dates_from_period(period: str):
    dates = []

    try:
        start, end = period.split(',')

        start_date = datetime.strptime(start, '%Y-%m-%d')
        end_date = datetime.strptime(end, '%Y-%m-%d')

        current_date = start_date
        while current_date <= end_date:
            dates.append(current_date)
            current_date = current_date + timedelta(days=1)

        return [d.strftime('%Y-%m-%d') for d in dates]

    except Exception as e:
        logging.error(e)
        return []


def _chunk_rows(rows, chunk_size):
    for start in range(0, len(rows), chunk_size):
        yield rows[start:start + chunk_size]


def _get_r5_metrics_path(date):
    return os.path.join(DIR_R5_METRICS, f'r5-metrics-{date}.csv')


def _load_maps(session):
    return {
        'pid': mount_pid_map(session),
        'language': mount_language_map(session),
        'format': mount_format_map(session),
        'localization': mount_localization_map(session),
        'localization_country': mount_localization_country_map(session),
        'issn': mount_issn_map(session),
    }


def _build_aggr_row(table_class, key, values):
    tii, tir, uii, uir = values
    row = {
        'total_item_investigations': tii,
        'total_item_requests': tir,
        'unique_item_investigations': uii,
        'unique_item_requests': uir,
    }

    if table_class.__tablename__ == 'aggr_article_journal_year_month_metric':
        collection, article_id, journal_id, year_month = key
        row.update({
            'collection': collection,
            'article_id': article_id,
            'journal_id': journal_id,
            'year_month': year_month,
        })
    elif table_class.__tablename__ == 'aggr_article_language_year_month_metric':
        collection, article_id, language_id, year_month = key
        row.update({
            'collection': collection,
            'article_id': article_id,
            'language_id': language_id,
            'year_month': year_month,
        })
    elif table_class.__tablename__ == 'aggr_journal_language_year_month_metric':
        collection, journal_id, language_id, year_month = key
        row.update({
            'collection': collection,
            'journal_id': journal_id,
            'language_id': language_id,
            'year_month': year_month,
        })
    elif table_class.__tablename__ == 'aggr_journal_language_yop_year_month_metric':
        collection, journal_id, language_id, yop, year_month = key
        row.update({
            'collection': collection,
            'journal_id': journal_id,
            'language_id': language_id,
            'yop': yop,
            'year_month': year_month,
        })
    elif table_class.__tablename__ == 'aggr_journal_geolocation_year_month_metric':
        collection, journal_id, country_code, year_month = key
        row.update({
            'collection': collection,
            'journal_id': journal_id,
            'country_code': country_code,
            'year_month': year_month,
        })
    elif table_class.__tablename__ == 'aggr_journal_geolocation_yop_year_month_metric':
        collection, journal_id, country_code, yop, year_month = key
        row.update({
            'collection': collection,
            'journal_id': journal_id,
            'country_code': country_code,
            'yop': yop,
            'year_month': year_month,
        })

    return row


def _persist_aggr_metrics_upsert(db_session, aggregated_metrics, table_class):
    if not aggregated_metrics:
        return True

    rows = [_build_aggr_row(table_class, key, values) for key, values in aggregated_metrics.items()]
    table = table_class.__table__

    for chunk in _chunk_rows(rows, SESSION_BULK_LIMIT):
        stmt = mysql_insert(table).values(chunk)
        update_mapping = {
            column: table.c[column] + getattr(stmt.inserted, column)
            for column in METRIC_COLUMNS
        }
        db_session.execute(stmt.on_duplicate_key_update(**update_mapping))

    db_session.commit()
    return True


def _load_day_aggregations(date, collection):
    metrics_path = _get_r5_metrics_path(date)
    if not os.path.exists(metrics_path):
        raise FileNotFoundError(f'Arquivo r5_metrics não encontrado: {metrics_path}')

    r5_metrics = read_r5_metrics(metrics_path)
    with SESSION_FACTORY() as dbsession:
        maps = _load_maps(dbsession)

    return {
        'aggr_article_journal_year_month_metric': _aggregate_by_keylist(
            r5_metrics,
            ['collection', 'idarticle', 'idjournal_cjm', 'year_month'],
            maps,
            collection,
        ),
        'aggr_article_language_year_month_metric': _aggregate_by_keylist(
            r5_metrics,
            ['collection', 'idarticle', 'idlanguage', 'year_month'],
            maps,
            collection,
        ),
        'aggr_journal_language_year_month_metric': _aggregate_by_keylist(
            r5_metrics,
            ['collection', 'idjournal_cjm', 'idlanguage', 'year_month'],
            maps,
            collection,
        ),
        'aggr_journal_language_yop_year_month_metric': _aggregate_by_keylist(
            r5_metrics,
            ['collection', 'idjournal_cjm', 'idlanguage', 'yop', 'year_month'],
            maps,
            collection,
        ),
        'aggr_journal_geolocation_year_month_metric': _aggregate_by_keylist(
            r5_metrics,
            ['collection', 'idjournal_cjm', 'country_code', 'year_month'],
            maps,
            collection,
        ),
        'aggr_journal_geolocation_yop_year_month_metric': _aggregate_by_keylist(
            r5_metrics,
            ['collection', 'idjournal_cjm', 'country_code', 'yop', 'year_month'],
            maps,
            collection,
        ),
    }


def _is_status_true(status):
    if isinstance(status, bool):
        return status

    if isinstance(status, LegacyCursorResult):
        if status._generate_rows:
            return True


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-c',
        '--collection',
        default=COLLECTION,
        help='Acrônimo de coleção'
    )

    parser.add_argument(
        '-p',
        '--period',
        help='Indica explicitamente o período de datas a serem agregadas (YYYY-MM-DD,YYYY-MM-DD)'
    )

    parser.add_argument(
        '--auto',
        action='store_true',
        default=False,
        help='Obtém por meio do banco de dados as datas cujos dados serão agregados'
    )

    parser.add_argument(
        '-t',
        '--tables',
        action='append',
        choices=[
            'aggr_article_journal_year_month_metric',
            'aggr_article_language_year_month_metric',
            'aggr_journal_language_year_month_metric',
            'aggr_journal_geolocation_year_month_metric',
            'aggr_journal_language_yop_year_month_metric',
            'aggr_journal_geolocation_yop_year_month_metric',
        ],
        default=[],
        help='Tabelas a serem preenchidas'
    )

    params = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] %(levelname)s %(message)s',
                        datefmt='%d/%b/%Y %H:%M:%S')

    logging.info(f'Detectando datas a serem agregadas...')
    with SESSION_FACTORY() as dbsession:
        dates = _extract_dates_from_period(params.period) if not params.auto else lib_database.get_dates_available_for_aggregation(dbsession, params.collection)
    
    tables = list(set(params.tables)) or TABLES_TO_UPDATE_DEFAULT

    logging.info(f'Há {len(dates)} data(s) e {len(tables)} tabela(s) a ser(em) agregada(s)')

    for date in dates:
        daily_aggr_data = None

        for table_name in tables:
            status_column_name = 'status_' + table_name

            try:
                with SESSION_FACTORY() as dbsession:
                    current_date_aggr_status_table = lib_database.get_aggr_status(dbsession, params.collection, date, status_column_name)
    
                if current_date_aggr_status_table == lib_status.AGGR_STATUS_QUEUE:
                    logging.info('Adicionando métricas agregadas para tabela %s e data (%s)...' % (table_name, date))

                    time_start = time.time()

                    if daily_aggr_data is None:
                        daily_aggr_data = _load_day_aggregations(date, params.collection)

                    if table_name == 'aggr_article_journal_year_month_metric':
                        with SESSION_FACTORY() as dbsession:
                            status = _persist_aggr_metrics_upsert(
                                dbsession,
                                daily_aggr_data[table_name],
                                AggrArticleJournalYearMonthMetric,
                            )

                    elif table_name == 'aggr_article_language_year_month_metric':
                        with SESSION_FACTORY() as dbsession:
                            status = _persist_aggr_metrics_upsert(
                                dbsession,
                                daily_aggr_data[table_name],
                                AggrArticleLanguageYearMonthMetric,
                            )

                    elif table_name == 'aggr_journal_language_year_month_metric':
                        with SESSION_FACTORY() as dbsession:
                            status = _persist_aggr_metrics_upsert(
                                dbsession,
                                daily_aggr_data[table_name],
                                AggrJournalLanguageYearMonthMetric,
                            )

                    elif table_name == 'aggr_journal_language_yop_year_month_metric':
                        with SESSION_FACTORY() as dbsession:
                            status = _persist_aggr_metrics_upsert(
                                dbsession,
                                daily_aggr_data[table_name],
                                AggrJournalLanguageYOPYearMonthMetric,
                            )

                    elif table_name == 'aggr_journal_geolocation_year_month_metric':
                        with SESSION_FACTORY() as dbsession:
                            status = _persist_aggr_metrics_upsert(
                                dbsession,
                                daily_aggr_data[table_name],
                                AggrJournalGeolocationYearMonthMetric,
                            )

                    elif table_name == 'aggr_journal_geolocation_yop_year_month_metric':
                        with SESSION_FACTORY() as dbsession:
                            status = _persist_aggr_metrics_upsert(
                                dbsession,
                                daily_aggr_data[table_name],
                                AggrJournalGeolocationYOPYearMonthMetric,
                            )

                    else:
                        status = None

                    if _is_status_true(status):
                        with SESSION_FACTORY() as dbsession:
                            lib_database.update_aggr_status_for_table(dbsession, params.collection, date, lib_status.AGGR_STATUS_DONE, status_column_name)
    
                    logging.info('Tempo total: %.2f segundos' % (time.time() - time_start))

                elif current_date_aggr_status_table is None:
                    logging.info('Data %s da coleção %s não está pronta para agregação' % (date, params.collection))
                    break

                else:
                    logging.info('Data %s da coleção %s já foi agregada para tabela %s' % (date, params.collection, table_name))

            except Exception as e:
                logging.error(e)
