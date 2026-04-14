import argparse
import logging
import os
from sqlalchemy import create_engine, and_
from sqlalchemy.orm import sessionmaker

from libs.lib_status import DATE_STATUS_COMPUTED, DATE_STATUS_PRETABLE
from models.declarative import DateStatus

STR_CONNECTION = os.environ.get('STR_CONNECTION', 'mysql://user:pass@localhost:3306/matomo')
COLLECTION = os.environ.get('COLLECTION', 'scl')

ENGINE = create_engine(STR_CONNECTION)
SESSION_FACTORY = sessionmaker(bind=ENGINE)

def main():
    parser = argparse.ArgumentParser(description="Reverte status de uma data específica para DATE_STATUS_PRETABLE (3) para reprocessamento de r5_metrics.")
    parser.add_argument('-c', '--collection', default=COLLECTION, help='Acrônimo da coleção')
    parser.add_argument('-d', '--dates', help='Lista de datas separadas por vírgula (ex: 2026-03-14,2026-03-15)')
    parser.add_argument('-a', '--all', action='store_true', help='Reverter todas as datas com status 4 (COMPUTED) para 3 (PRETABLE) na coleção.')
    params = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] %(levelname)s %(message)s',
                        datefmt='%d/%b/%Y %H:%M:%S')

    session = SESSION_FACTORY()
    
    if params.all:
        dates_found = session.query(DateStatus).filter(
            and_(
                DateStatus.collection == params.collection, 
                DateStatus.status == DATE_STATUS_COMPUTED
            )
        ).all()
        logging.info(f"Modo --all ativado. Buscando todas as datas com status {DATE_STATUS_COMPUTED} para a coleção {params.collection}.")
    elif params.dates:
        dates_to_recover = params.dates.split(',')
        dates_found = session.query(DateStatus).filter(
            and_(
                DateStatus.collection == params.collection, 
                DateStatus.date.in_(dates_to_recover),
                DateStatus.status == DATE_STATUS_COMPUTED
            )
        ).all()
        logging.info(f"Buscando datas específicas: {params.dates}")
    else:
        logging.error("Você deve fornecer --dates ou usar a flag --all.")
        return

    logging.info(f"Encontradas {len(dates_found)} datas com status {DATE_STATUS_COMPUTED} dentre as solicitadas para a coleção {params.collection}.")

    reverted_count = 0

    for ds in dates_found:
        logging.info(f"Revertendo status da data {ds.date} de {DATE_STATUS_COMPUTED} para {DATE_STATUS_PRETABLE} (reprocessamento de métricas).")
        ds.status = DATE_STATUS_PRETABLE
        reverted_count += 1
    
    if reverted_count > 0:
        session.commit()
        logging.info(f"Foram revertidas {reverted_count} datas.")
    else:
        logging.info("Nenhuma data precisou ser revertida (verifique se já não estão no status correto ou se as datas estão incorretas).")

    session.close()

if __name__ == '__main__':
    main()
