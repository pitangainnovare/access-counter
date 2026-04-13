import argparse
import logging
import os
import time

import reverse_geocode

from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import sessionmaker

from models.declarative import Localization, LocalizationCountry


STR_CONNECTION = os.environ.get('STR_CONNECTION', 'mysql://user:pass@localhost:3306/matomo')
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')
BATCH_SIZE = int(os.environ.get('LOCALIZATION_COUNTRY_BATCH_SIZE', '5000'))
UNKNOWN_COUNTRY_CODE = os.environ.get('UNKNOWN_COUNTRY_CODE', 'ZZ').upper()
if len(UNKNOWN_COUNTRY_CODE) != 2:
    UNKNOWN_COUNTRY_CODE = 'ZZ'


def _create_table(engine):
    LocalizationCountry.__table__.create(bind=engine, checkfirst=True)


def _get_pending_localizations(session, batch_size):
    query = (
        session.query(Localization.id, Localization.latitude, Localization.longitude)
        .outerjoin(LocalizationCountry, LocalizationCountry.idlocalization == Localization.id)
        .filter(LocalizationCountry.idlocalization.is_(None))
        .filter(Localization.latitude.isnot(None))
        .filter(Localization.longitude.isnot(None))
        .order_by(Localization.id)
        .limit(batch_size)
    )

    return query.all()


def _normalize_country_code(result):
    country_code = (result or {}).get('country_code', UNKNOWN_COUNTRY_CODE)
    country_code = country_code.upper() if isinstance(country_code, str) else UNKNOWN_COUNTRY_CODE
    if len(country_code) != 2:
        return UNKNOWN_COUNTRY_CODE
    return country_code


def _map_localizations(rows):
    coordinates = [[float(row.latitude), float(row.longitude)] for row in rows]
    reverse_results = reverse_geocode.search(coordinates) if coordinates else []

    mapped_rows = []
    for row, result in zip(rows, reverse_results):
        mapped_rows.append({
            'idlocalization': row.id,
            'country_code': _normalize_country_code(result),
        })

    if len(mapped_rows) != len(rows):
        logging.warning(
            'Nem todas as localizações retornaram resultado de geocodificação (%s/%s). '
            'Completando faltantes com %s.',
            len(mapped_rows),
            len(rows),
            UNKNOWN_COUNTRY_CODE,
        )

        mapped_ids = {item['idlocalization'] for item in mapped_rows}
        for row in rows:
            if row.id not in mapped_ids:
                mapped_rows.append({
                    'idlocalization': row.id,
                    'country_code': UNKNOWN_COUNTRY_CODE,
                })

    return mapped_rows


def _upsert_rows(session, rows):
    if not rows:
        return 0

    insert_stmt = mysql_insert(LocalizationCountry).values(rows)
    upsert_stmt = insert_stmt.on_duplicate_key_update(
        country_code=insert_stmt.inserted.country_code,
    )
    session.execute(upsert_stmt)
    session.commit()
    return len(rows)


def _count_rows(session, model):
    return session.query(model).count()


def _count_unknown_country_codes(session):
    return (
        session.query(LocalizationCountry)
        .filter(LocalizationCountry.country_code == UNKNOWN_COUNTRY_CODE)
        .count()
    )


def main():
    usage = 'Cria e popula a tabela counter_localization_country com o país de cada localização.'
    parser = argparse.ArgumentParser(usage)

    parser.add_argument(
        '-u', '--str_connection',
        default=STR_CONNECTION,
        help='String de conexão com banco de dados (mysql://username:password@host:port/database)'
    )

    parser.add_argument(
        '--batch_size',
        type=int,
        default=BATCH_SIZE,
        help='Quantidade de registros processados por lote'
    )

    parser.add_argument(
        '--max_batches',
        type=int,
        default=0,
        help='Quantidade máxima de lotes a processar. Use 0 para processar até o fim'
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

    engine = create_engine(params.str_connection, pool_recycle=1800)
    session_factory = sessionmaker(bind=engine)
    _create_table(engine)

    total_processed = 0
    batch_counter = 0
    time_start = time.time()

    while True:
        if params.max_batches and batch_counter >= params.max_batches:
            break

        with session_factory() as session:
            rows = _get_pending_localizations(session, params.batch_size)

        if not rows:
            break

        mapped_rows = _map_localizations(rows)

        with session_factory() as session:
            inserted_rows = _upsert_rows(session, mapped_rows)

        batch_counter += 1
        total_processed += inserted_rows

        logging.info(
            'Lote %s processado: %s localizações inseridas/atualizadas (último idlocalization=%s)',
            batch_counter,
            inserted_rows,
            rows[-1].id,
        )

    with session_factory() as session:
        total_localizations = _count_rows(session, Localization)
        total_country_rows = _count_rows(session, LocalizationCountry)
        total_unknown = _count_unknown_country_codes(session)

    logging.info('Processamento concluído em %.2f segundos', time.time() - time_start)
    logging.info('Total de localizações na origem: %s', total_localizations)
    logging.info('Total de localizações com país: %s', total_country_rows)
    logging.info('Total de localizações com country_code=%s: %s', UNKNOWN_COUNTRY_CODE, total_unknown)
    logging.info('Total processado nesta execução: %s', total_processed)


if __name__ == '__main__':
    main()
