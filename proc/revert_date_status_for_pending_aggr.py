import argparse
import logging
import os
from sqlalchemy import create_engine, and_
from sqlalchemy.orm import sessionmaker

from libs.lib_status import DATE_STATUS_COMPUTED, DATE_STATUS_COMPLETED
from models.declarative import DateStatus, AggrStatus

STR_CONNECTION = os.environ.get('STR_CONNECTION', 'mysql://user:pass@localhost:3306/matomo')
COLLECTION = os.environ.get('COLLECTION', 'scl')

ENGINE = create_engine(STR_CONNECTION)
SESSION_FACTORY = sessionmaker(bind=ENGINE)

def main():
    parser = argparse.ArgumentParser(description="Reverte status de DATE_STATUS_COMPLETED para DATE_STATUS_COMPUTED caso agregações não estejam concluídas.")
    parser.add_argument('-c', '--collection', default=COLLECTION, help='Acrônimo da coleção')
    params = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] %(levelname)s %(message)s',
                        datefmt='%d/%b/%Y %H:%M:%S')

    session = SESSION_FACTORY()

    # Pega todas as datas com status = 5 (COMPLETED) para a coleção
    dates_completed = session.query(DateStatus).filter(
        and_(DateStatus.collection == params.collection, DateStatus.status == DATE_STATUS_COMPLETED)
    ).all()

    logging.info(f"Encontradas {len(dates_completed)} datas com status {DATE_STATUS_COMPLETED} para a coleção {params.collection}.")

    reverted_count = 0

    for ds in dates_completed:
        # Pega o status de agregação correspondente
        aggr = session.query(AggrStatus).filter(
            and_(AggrStatus.collection == params.collection, AggrStatus.date == ds.date)
        ).first()

        # Se não existe registro de agregação ou alguma não está concluída, reverte
        aggr_completed = False
        if aggr:
            aggr_completed = all([
                getattr(aggr, 'status_aggr_article_journal_year_month_metric', 0) == 1,
                getattr(aggr, 'status_aggr_article_language_year_month_metric', 0) == 1,
                getattr(aggr, 'status_aggr_journal_language_year_month_metric', 0) == 1,
                getattr(aggr, 'status_aggr_journal_geolocation_year_month_metric', 0) == 1,
                getattr(aggr, 'status_aggr_journal_language_yop_year_month_metric', 0) == 1,
                getattr(aggr, 'status_aggr_journal_geolocation_yop_year_month_metric', 0) == 1,
            ])
        
        if not aggr_completed:
            logging.info(f"Revertendo status da data {ds.date} para {DATE_STATUS_COMPUTED} (agregações pendentes).")
            ds.status = DATE_STATUS_COMPUTED
            reverted_count += 1
    
    if reverted_count > 0:
        session.commit()
        logging.info(f"Foram revertidas {reverted_count} datas.")
    else:
        logging.info("Nenhuma data precisou ser revertida.")

    session.close()

if __name__ == '__main__':
    main()