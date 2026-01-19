import argparse
import csv
import datetime
import logging
import os
import re
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from decimal import Decimal
from libs.lib_database import (
    update_date_status,
    get_date_status,
    update_date_metric_status,
    compute_date_metric_status,
    get_missing_aggregations
)
from libs.lib_status import DATE_STATUS_COMPLETED, DATE_STATUS_COMPUTED
from proc.calculate_metrics import get_date_from_file_path
from utils.regular_expressions import REGEX_ISSN, REGEX_ARTICLE_PID
from libs import lib_database
from models.declarative import (
    Journal,
    JournalCollection,
    Localization,
    ArticleFormat,
    ArticleLanguage,
    Article,
    ArticleMetric,
    JournalMetric,
    SushiJournalMetric,
    SushiJournalYOPMetric,
    SushiArticleMetric
)


MATOMO_DATABASE_STRING = os.environ.get('MATOMO_DATABASE_STRING', 'mysql://user:pass@localhost:3306/matomo')
COLLECTION = os.environ.get('COLLECTION', 'scl')
MIN_YEAR = int(os.environ.get('MIN_YEAR', '1900'))
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')
TABLES_TO_PERSIST = os.environ.get('TABLES_TO_PERSIST', 'counter_foreign,counter_article_metric,counter_journal_metric,sushi_article_metric,sushi_journal_metric,sushi_journal_yop_metric')

DIR_R5_METRICS = os.environ.get('DIR_R5_METRICS', '/app/data/r5')
DIR_R5_METRICS_TO_REPAIR = os.path.join(DIR_R5_METRICS, 'to_repair')
MAX_YEAR = datetime.datetime.now().year + 5

ENGINE = create_engine(MATOMO_DATABASE_STRING, pool_recycle=1800)
SESSION_FACTORY = sessionmaker(bind=ENGINE)
SESSION_BULK_LIMIT = int(os.environ.get('SESSION_BULK_LIMIT', '500'))


class R5Metrics:
    def __init__(self, **kargs):
        self.collection = COLLECTION
        self.pid = kargs['pid']
        self.format_name = kargs['format_name']
        self.language_name = kargs['language_name'] if kargs['language_name'] else 'und'
        self.latitude = round(Decimal(kargs['latitude']), 3) if kargs['latitude'] != 'NULL' else kargs['latitude']
        self.longitude = round(Decimal(kargs['longitude']), 3) if kargs['longitude'] != 'NULL' else kargs['longitude']
        self.year_of_publication = int(kargs['year_of_publication']) if kargs['year_of_publication'].isdigit() else kargs['year_of_publication']
        self.issn = kargs['issn']
        self.year_month_day = kargs['year_month_day']
        self.total_item_investigations = int(kargs['total_item_investigations'])
        self.total_item_requests = int(kargs['total_item_requests'])
        self.unique_item_investigations = int(kargs['unique_item_investigations'])
        self.unique_item_requests = int(kargs['unique_item_requests'])

    def is_valid_metric(self):
        # Ignora métrica que latitude é nula
        if self.latitude in {'NULL', ''} or not self.latitude:
            return False

        # Ignora métrica que longitude é nula
        if self.longitude in {'NULL', ''} or not self.longitude:
            return False

        # Ignora métrica cujo PID é mal-formado e que seja diferente das coleções Preprints e Saúde Pública
        if self.collection not in {'pre', 'ssp'}:
            if '-' in self.pid:
                if not re.match(REGEX_ARTICLE_PID, self.pid):
                    return False
            else:
                if len(self.pid) != 23:
                    return False

        # Ignora métrica cujo PID é vazio e que ou seja da coleção Preprints ou seja da coleção Saúde Pública
        if self.collection in {'pre', 'ssp'}:
            if not self.pid:
                return False

        # Ignora métricas cujo artigo possui ano de publicação inválido
        if not isinstance(self.year_of_publication, int):
            return False
        elif self.year_of_publication < MIN_YEAR or self.year_of_publication > MAX_YEAR:
            return False

        return True

    def __str__(self):
        return '|'.join([str(getattr(self, k)) for k in self.__dict__ if not k.startswith('_')])


def sum_metrics(m1, m2):
    """
    Retorna uma nova lista com a soma dos elementos de duas listas de métricas
    :param m1: Lista de métricas 1
    :param m2: Lista de métricas 2
    :return: Lista em que cada elemento é a soma dos elementos das listas m1 e m2
    """
    return [m1[x] + m2[x] for x in range(4)]


def mount_issn_map(session):
    """
    Cria mapa de ISSN chave para ISSN valor, com base na banco de dados COUNTER
    :param session: Sessão de conexão com banco de dados COUNTER
    :return: Um dicionário que mapeia ISSN-Valor a ISSN-Chave da tabela counter_journal
    """
    issn_map = {}

    for j in session.query(Journal):
        issns = [i for i in [j.pid_issn,
                 j.online_issn,
                 j.print_issn] if i != '']
        for i in issns:
            if i not in issn_map:
                issn_map[i] = j.id
            else:
                if issn_map[i] != j.id:
                    logging.error('Base de periódicos está inconsistente (%s -> %s,  %s -> %s)'
                                  % (i, j.id, i, issn_map[i]))

    return issn_map


def mount_localization_map(session):
    """
    Cria mapa de (latitude, longitude) para ID de localização na base de dados
    :param session: Sessão de conexão com banco de dados COUNTER
    :return: Um dicionário que mapeia (latitude, longitude) a ID de localização
    """
    localization_map = {}

    for m in session.query(Localization):
        localization_map[(m.latitude, m.longitude)] = m.id
    return localization_map


def mount_format_map(session):
    """
    Cria mapa de nome de formato para ID de formato na base de dados
    :param session: Sessão de conexão com banco de dados COUNTER
    :return: Um dicionário que mapeia nome de formato a ID de formato
    """
    format_map = {}

    for m in session.query(ArticleFormat):
        format_code = m.id
        format_name = m.format
        format_map[format_name] = format_code

    return format_map


def mount_pid_map(session):
    """
    Cria mapa de PID e COLLECTION ACRONYM a ID na base de dados
    :param session: Sessão de conexão com banco de dados COUNTER
    :return: Um dicionário que mapeia PID e COLLECTION a ID
    """
    pid_map = {}

    for m in session.query(Article):
        article_id = m.id
        article_pid = m.pid
        article_collection = m.collection
        pid_map[(article_pid, article_collection)] = article_id

    return pid_map


def mount_language_map(session):
    """
    Cria mapa de nome de idioma para ID de idioma na base de dados
    :param session: Sessão de conexão com banco de dados COUNTER
    :return: Um dicionário que mapeia nome de idioma a ID de idioma
    """
    language_map = {}

    for m in session.query(ArticleLanguage):
        language_id = m.id
        language_name = m.language
        language_map[language_name] = language_id

    return language_map


def read_r5_metrics(path_file_r5_metrics):
    """
    Processa arquivo r5_metrics para uma lista de instâncias R5Metrics
    :param path_file_r5_metrics: Caminho de arquivo r5_metrics
    :return: Lista de instâncias R5Metrics
    """
    r5_metrics = []
    with open(path_file_r5_metrics) as fi:
        csv_reader = csv.DictReader(fi, delimiter='|', fieldnames=['pid',
                                                                   'format_name',
                                                                   'language_name',
                                                                   'latitude',
                                                                   'longitude',
                                                                   'year_of_publication',
                                                                   'issn',
                                                                   'year_month_day',
                                                                   'total_item_investigations',
                                                                   'total_item_requests',
                                                                   'unique_item_investigations',
                                                                   'unique_item_requests'])
        for row in csv_reader:
            r5 = R5Metrics(**row)
            if r5.is_valid_metric():
                r5_metrics.append(R5Metrics(**row))
            else:
                logging.debug('Métrica ignorada: %s' % r5.__str__())

    return r5_metrics


def update_issn_map(r5_metrics, issn_map):
    """
    Obtém ISSNs da lista de métricas calculadas
    :param r5_metrics: lista de instâncias R5Metric
    :param issn_map: Dicionário que mapeia ISSNs
    :return: Um dicionário de ISSNs faltantes a serem inseridos na base de dados
    """
    new_issns = set()
    for r in r5_metrics:
        m = re.match(REGEX_ISSN, r.issn or "")
        if not m:
            continue

        issn = m.string
        if issn and issn not in issn_map:
            issn_map[issn] = issn
            new_issns.add(issn)
    return new_issns


def update_issn_table(issns, db_session):
    """
    Atualiza banco de dados COUNTER com novos periódicos
    :param issns: Lista de ISSNs que não constam no banco de dados
    :param db_session: Sessão de conexão com banco de dados
    """
    logging.info('Há %d periódico(s) a ser(em) adicionado(s) no banco de dados' % len(issns))
    for issn in issns:
        new_journal = Journal()
        new_journal.pid_issn = issn
        new_journal.print_issn = ''
        new_journal.online_issn = issn

        db_session.add(new_journal)
        db_session.flush()

        new_journal_collection = JournalCollection()
        new_journal_collection.idjournal_jc = new_journal.id
        new_journal_collection.collection = COLLECTION
        new_journal_collection.title = ''

        db_session.add(new_journal_collection)
        db_session.flush()

        logging.info('Adicionado periódico %s' % issn)
    db_session.commit()


def update_localization_table(r5_metrics, db_session, localization_map):
    """
    Atualiza banco de dados com novas localizações, isto é, pares (latitude, longitude)
    :param r5_metrics: lista de instâncias R5Metric
    :param db_session: Sessão de conexão com banco de dados
    :param localization_map: Dicionário que mapeia (latitude, longitude) a seu respectivo código no banco de dados
    """
    all_localizations = set([(r.latitude, r.longitude) for r in r5_metrics])
    new_localizations = all_localizations.difference(set(localization_map.keys()))

    objects = []
    for ngeo in new_localizations:
        latitude, longitude = ngeo

        new_localization = Localization()
        new_localization.latitude = latitude
        new_localization.longitude = longitude

        localization_map[(latitude, longitude)] = None
        objects.append(new_localization)

    db_session.bulk_save_objects(objects)
    db_session.commit()

    # Recarrega mapa de localização
    if new_localizations:
        return True


def update_format_table(r5_metrics, db_session, format_map):
    """
    Atualiza banco de dados com novos formatos
    :param r5_metrics: lista de instâncias R5Metric
    :param db_session: Sessão de conexão com banco de dados
    :param format_map: Dicionário que mapeia formato a seu respectivo código no banco de dados
    """
    all_formats = set([r.format_name for r in r5_metrics])
    new_formats = all_formats.difference(set(format_map.keys()))

    for nfmt in new_formats:
        new_fmt = ArticleFormat()
        new_fmt.format = nfmt

        logging.info('Adicionado formato %s' % nfmt)
        db_session.add(new_fmt)
        db_session.commit()

        format_map[new_fmt.format] = new_fmt.id


def update_language_table(r5_metrics, db_session, language_map):
    """
    Atualiza banco de dados com novos idiomas
    :param r5_metrics: lista de instâncias R5Metric
    :param db_session: Sessão de conexão com banco de dados
    :param language_map: Dicionário que mapeia idioma a seu respectivo código no banco de dados
    """
    all_languages = set([r.language_name for r in r5_metrics])
    new_languages = all_languages.difference(set(language_map.keys()))

    for nlang in new_languages:
        new_language = ArticleLanguage()
        new_language.language = nlang

        logging.info('Adicionado idioma %s' % nlang)
        db_session.add(new_language)
        db_session.commit()

        language_map[new_language.language] = new_language.id


def update_article_table(r5_metrics, db_session, issn_map, pid_map):
    """
    Atualiza banco de dados com novos artigos
    :param r5_metrics: lista de instâncias R5Metric
    :param db_session: Sessão de conexão com banco de dados
    :param issn_map: Dicionário que mapeia ISSNs
    :param pid_map: Dicionário que mapeia PID e COLLECTION a código no banco de dados
    """
    all_pids = set([(r.pid, COLLECTION) for r in r5_metrics])
    new_pids = all_pids.difference(set(pid_map.keys()))

    pid_to_r5 = {}
    for r in r5_metrics:
        if (r.pid, COLLECTION) in new_pids:
            pid_to_r5[(r.pid, COLLECTION)] = r

    objects = []
    for k, v in pid_to_r5.items():
        pid, col = k
        if (pid, col) not in pid_map:
            new_article = Article()
            new_article.collection = COLLECTION
            new_article.idjournal_a = issn_map[v.issn]
            new_article.pid = v.pid
            new_article.yop = v.year_of_publication

            pid_map[(pid, col)] = None
            objects.append(new_article)

    db_session.bulk_save_objects(objects)
    db_session.commit()

    # Recarrega mapa de PID e COLLECTION
    if new_pids:
        return True


def persist_metrics(r5_metrics, db_session, maps, key_list, table_class, collection):
    """
    Adiciona métricas no banco de dados
    :param r5_metrics: lista de instâncias R5Metric
    :param db_session: Sessão de conexão com banco de dados
    :param maps: Dicionários que mapeiam insumos a seus respectivos IDs no banco de dados
    :param key_list: Lista de chaves
    :param table_class: Classe que representa a tabela a ser persistida
    :param collection: acrônimo da coleção
    """
    # Retorna Trua caso não existam dados a serem gravados
    if len(r5_metrics) == 0:
        return True

    objects = []

    # Obtém um dicionário de métricas agregadas pelos valores associados a chave de key_list
    aggregated_metrics = _aggregate_by_keylist(r5_metrics, key_list, maps)

    # Data das métricas
    year_month_day = r5_metrics[0].year_month_day

    # Obtém último ID
    try:
        last_id = lib_database.get_last_id(db_session, table_class)
        next_id = 1 + last_id if last_id else 1
    except OperationalError:
        _dump_repairing_data(year_month_day, key_list)
        return False

    # Transforma dicionário de métricas em itens persistíveis no banco de dados
    for k, v in aggregated_metrics.items():
        row = {}
        tii, tir, uii, uir = v
        row.update({'id': next_id,
                    'total_item_investigations': tii,
                    'total_item_requests': tir,
                    'unique_item_investigations': uii,
                    'unique_item_requests': uir})

        # É métrica agregada para artigo
        if table_class.__tablename__ == 'counter_article_metric':
            idarticle, idlanguage, idformat, idlocalization, year_month_day = k
            row.update({'idarticle': idarticle,
                        'idlanguage': idlanguage,
                        'idformat': idformat,
                        'idlocalization': idlocalization,
                        'year_month_day': year_month_day})

        # É métrica agregada para periódico
        elif table_class.__tablename__ == 'counter_journal_metric':
            idjournal_cjm, idlanguage_cjm, idformat_cjm, yop, year_month_day = k
            row.update({'idjournal_cjm': idjournal_cjm,
                        'idlanguage_cjm': idlanguage_cjm,
                        'idformat_cjm': idformat_cjm,
                        'yop': yop,
                        'year_month_day': year_month_day,
                        'collection': collection})

        # É métrica agregada para periódico e ano de publicação na tabela SUSHI
        elif table_class.__tablename__ == 'sushi_journal_yop_metric':
            idjournal_sjym, yop, year_month_day = k
            row.update({'idjournal_sjym': idjournal_sjym,
                        'yop': yop,
                        'year_month_day': year_month_day,
                        'collection': collection})

        # É métrica agregada para periódico na tabela SUSHI
        elif table_class.__tablename__ == 'sushi_journal_metric':
            idjournal_sjm, year_month_day = k
            row.update({'idjournal_sjm': idjournal_sjm,
                        'year_month_day': year_month_day,
                        'collection': collection})

        # É métrica agregada para artigo na tabela SUSHI
        elif table_class.__tablename__ == 'sushi_article_metric':
            idarticle_sam, year_month_day = k
            row.update({'idarticle_sam': idarticle_sam,
                        'year_month_day': year_month_day})

        objects.append(row)
        next_id += 1

        if len(objects) >= SESSION_BULK_LIMIT:
            try:
                db_session.bulk_insert_mappings(table_class, objects)
                db_session.commit()
                objects = []
            except OperationalError:
                _dump_repairing_data(year_month_day, key_list)
                return False
    try:
        db_session.bulk_insert_mappings(table_class, objects)
        db_session.commit()
    except OperationalError:
        _dump_repairing_data(year_month_day, key_list)
        return False

    return True


def _aggregate_by_keylist(r5_metrics, key_list, maps):
    """
    Agrega métricas de acordo com uma lista de chaves de agregação

    :param r5_metrics: Lista de métricas do tipo R5Metric
    :param key_list:  Lista de chaves agregadoras
    :param maps: Dicionários que mapeiam insumos a seus respectivos IDs no banco de dados
    :return: Um dicionário que mapeia as métricas a suas respectivas chaves
    """
    aggregated_metrics = {}

    for r in r5_metrics:
        if r.issn == "" or r.issn not in maps["issn"]:
            logging.warning("ISSN vazio em %s", r)
            continue

        attrs = {'collection': COLLECTION,
                 'idjournal_cjm': maps['issn'][r.issn],
                 'idjournal_sjm': maps['issn'][r.issn],
                 'idjournal_sjym': maps['issn'][r.issn],
                 'idarticle': maps['pid'][(r.pid, COLLECTION)],
                 'idarticle_sam': maps['pid'][(r.pid, COLLECTION)],
                 'idlanguage': maps['language'][r.language_name],
                 'idlanguage_cjm': maps['language'][r.language_name],
                 'idformat': maps['format'][r.format_name],
                 'idformat_cjm': maps['format'][r.format_name],
                 'idlocalization': maps['localization'][(r.latitude, r.longitude)],
                 'yop': r.year_of_publication,
                 'year_month_day': r.year_month_day}

        # Monta chave de agregação de métricas
        key = tuple(attrs[k] for k in key_list)

        # Adiciona métricas no dicionário
        if key not in aggregated_metrics:
            aggregated_metrics[key] = [r.total_item_investigations,
                                       r.total_item_requests,
                                       r.unique_item_investigations,
                                       r.unique_item_requests]
        else:
            # Caso key já esteja no dicionário de métricas, faz a somatória dos valores
            aggregated_metrics[key] = sum_metrics(aggregated_metrics[key], [r.total_item_investigations,
                                                                            r.total_item_requests,
                                                                            r.unique_item_investigations,
                                                                            r.unique_item_requests])
    return aggregated_metrics


def _dump_repairing_data(year_month_day, keys):
    logging.error('It was not possible to persist metrics. Dumping repairing data %s' % year_month_day)
    repair_file_path = os.path.join(DIR_R5_METRICS_TO_REPAIR,
                                    COLLECTION + '.csv')
    with open(repair_file_path, 'a') as file:
        file.write('\t'.join([year_month_day] + keys) + '\n')


def check_repairing_files():
    keys_to_table = {'\t'.join(['idarticle',
                                'idlanguage',
                                'idformat',
                                'idlocalization',
                                'year_month_day']): 'counter_article_metric',
                     '\t'.join(['idjournal_cjm',
                                'idlanguage_cjm',
                                'idformat_cjm',
                                'yop',
                                'year_month_day']): 'counter_journal_metric',
                     '\t'.join(['idjournal_sjym',
                                'yop',
                                'year_month_day']): 'sushi_journal_yop_metric',
                     '\t'.join(['idjournal_sjm',
                                'year_month_day']): 'sushi_journal_metric',
                     '\t'.join(['idarticle_sam',
                                'year_month_day']): 'sushi_article_metric'}

    repairing_files = os.listdir(DIR_R5_METRICS_TO_REPAIR)
    logging.info('There are(is) %d file(s)' % len(repairing_files))

    for rf in repairing_files:
        rf_full_path = os.path.join(DIR_R5_METRICS_TO_REPAIR, rf)
        fixing_task_failed = False

        logging.info('Repairing tables about file %s' % rf_full_path)
        with open(rf_full_path) as f:
            for row in f:
                collection = rf.replace('.csv', '')
                els = row.strip().split('\t')
                date = els[0]
                keys = '\t'.join(els[1:])

                table = keys_to_table.get(keys, '')

                if table:
                    if table in {'sushi_journal_metric', 'sushi_journal_yop_metric', 'counter_journal_metric'}:
                        raw_query = 'DELETE FROM {0} WHERE collection = "{1}" AND year_month_day = "{2}";'.format(table, collection, date)
                    elif table == 'sushi_article_metric':
                        raw_query = 'DELETE FROM {0} WHERE year_month_day = "{1}" AND idarticle_sam IN (SELECT DISTINCT id FROM counter_article WHERE collection = "{2}");'.format(table, date, collection)
                    elif table == 'counter_article_metric':
                        raw_query = 'DELETE FROM {0} WHERE year_month_day = "{1}" AND idarticle IN (SELECT DISTINCT id FROM counter_article WHERE collection = "{2}");'.format(table, date, collection)

                    try:
                        logging.info('Executing query to fix (%s, %s, %s)...' % (collection, table, date))
                        ENGINE.execute(raw_query)

                        logging.info('Fixing control_date_status table...')
                        raw_query_cds = 'UPDATE control_date_status SET status_{0} = 0, status = 4 WHERE collection = "{1}" and date = "{2}";'.format(table, collection, date)
                        ENGINE.execute(raw_query_cds)
                    except:
                        logging.error('Failed to fix tables')
                        fixing_task_failed = True

        if not fixing_task_failed:
            logging.info('Removing file %s...' % rf_full_path)
            os.remove(rf_full_path)


def get_files_to_persist(dir_r5_metrics, db_session):
    files_to_persist = []

    try:
        files_dates = sorted([f for f in os.listdir(dir_r5_metrics) if 'r5-metrics' in f])

        for f in files_dates:
            f_date = get_date_from_file_path(f)
            f_status = get_date_status(db_session, COLLECTION, f_date)

            if f_status == DATE_STATUS_COMPUTED:
                files_to_persist.append(os.path.join(dir_r5_metrics, f))
            elif f_status > DATE_STATUS_COMPUTED:
                logging.warning('Data %s já está persistida na base de dados' % f)
            elif f_status < DATE_STATUS_COMPUTED:
                logging.warning('Data %s não contém métricas calculadas' % f)
    except FileNotFoundError:
        logging.error('Diretório %s não existe' % dir_r5_metrics)

    return files_to_persist


def need_to_update_memory_data(target_tables):
    for t in ['counter_article_metric',
              'counter_journal_metric',
              'sushi_article_metric',
              'sushi_journal_metric',
              'sushi_journal_yop_metric']:
        if t in target_tables:
            return True
    return False


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-u', '--matomo_db_uri',
        default=MATOMO_DATABASE_STRING,
        dest='matomo_db_uri',
        help='String de conexão a base SQL Matomo no formato mysql://username:password@host1:port/database'
    )

    parser.add_argument(
        '-d', '--dir_r5_metrics',
        default=DIR_R5_METRICS,
        dest='dir_r5_metrics',
        help='Diretório com arquivos r5_metrics'
    )

    parser.add_argument(
        '-t', '--tables',
        dest='tables',
        default=TABLES_TO_PERSIST,
        type=str,
        help='Lista de tabelas a serem persistidas (indicar os nomes das tabelas separados por vírgula). '
             'Por padrão é o valor da variável de ambiente TABLES_TO_PERSIST ou '
             'counter_foreign,counter_journal_metric,sushi_journal_metric,sushi_journal_yop_metric'
    )

    parser.add_argument(
        '--auto',
        dest='auto',
        action='store_true',
        default=False,
        help='Obtém por meio do banco de dados os nomes das tabelas de agregação a serem persistidas'
    )

    parser.add_argument(
        '-i', '--ignore_counter_metric_tables',
        dest='ignore_counter_metric_tables',
        action='store_true',
        default=False,
        help='Não persiste dados nas tabelas counter_article_metric e counter_journal_metric'
    )

    params = parser.parse_args()

    if not os.path.exists(DIR_R5_METRICS_TO_REPAIR):
        os.makedirs(DIR_R5_METRICS_TO_REPAIR)

    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] %(levelname)s %(message)s',
                        datefmt='%d/%b/%Y %H:%M:%S')

    logging.info('Checking repairing files...')
    check_repairing_files()

    # Obtém dicionários que mapeia ISSN a ISSN-Chave, Idioma a ID e Formato a ID
    issn_map = mount_issn_map(SESSION_FACTORY())
    localization_map = mount_localization_map(SESSION_FACTORY())
    language_map = mount_language_map(SESSION_FACTORY())
    format_map = mount_format_map(SESSION_FACTORY())
    pid_map = mount_pid_map(SESSION_FACTORY())

    # Obtém lista de arquivos r5_metrics a serem lidos
    files_r5 = sorted(get_files_to_persist(params.dir_r5_metrics, SESSION_FACTORY()))
    logging.info('Há %d arquivo(s) para ser(em) processado(s)' % len(files_r5))

    for f in files_r5:
        time_start = time.time()
        logging.info('Processando arquivo %s' % f)

        f_date = get_date_from_file_path(f)

        # Lê arquivo r5
        logging.info('Convertendo arquivo para R5Metric...')
        r5_metrics = read_r5_metrics(f)

        # Obtém os nomes das tabelas a serem persistidas
        if params.auto:
            target_tables = ['counter_foreign']
            target_tables.extend(get_missing_aggregations(SESSION_FACTORY(), COLLECTION, f_date))
        else:
            target_tables = params.tables.split(',')

        logging.info('Tabelas a serem persistidas: (%s)' % ','.join(target_tables))

        # Obtém lista de ISSNs que não existem no banco de dados
        if 'counter_foreign' in target_tables:
            logging.info('Obtendo ISSNs...')
            new_issns = update_issn_map(r5_metrics, issn_map)

            # Atualiza banco de dados com ISSNs não encontrados
            if new_issns:
                logging.info('Atualizando lista de ISSNs...')
                update_issn_table(new_issns, SESSION_FACTORY())
                issn_map = mount_issn_map(SESSION_FACTORY())

            # Atualiza lista de pares (Latitude, Longitude) no banco de dados
            logging.info('Atualizando lista de pares (latitude, longitude)...')
            exist_new_localizations = update_localization_table(r5_metrics, SESSION_FACTORY(), localization_map)

            if exist_new_localizations and need_to_update_memory_data(target_tables):
                logging.info('Recarregando dados de localização')
                localization_map = mount_localization_map(SESSION_FACTORY())

            # Atualiza formatos de artigo no banco de dados
            logging.info('Atualizando formatos...')
            update_format_table(r5_metrics, SESSION_FACTORY(), format_map)

            # Atualiza idiomas de artigo no banco de dados
            logging.info('Atualizando idiomas...')
            update_language_table(r5_metrics, SESSION_FACTORY(), language_map)

            # Atualiza artigos no banco de dados
            logging.info('Atualizando artigos...')
            exist_new_pids = update_article_table(r5_metrics, SESSION_FACTORY(), issn_map, pid_map)

            if exist_new_pids and need_to_update_memory_data(target_tables):
                logging.info('Recarregando dados de PID e COLLECTION')
                pid_map = mount_pid_map(SESSION_FACTORY())

        maps = {'pid': pid_map, 'language': language_map, 'format': format_map, 'localization': localization_map, 'issn': issn_map}

        if 'counter_article_metric' in target_tables and not params.ignore_counter_metric_tables:
            logging.info('Adicionando métricas agregadas para counter_article...')
            keys_counter_article = ['idarticle', 'idlanguage', 'idformat', 'idlocalization', 'year_month_day']
            cam_status = persist_metrics(r5_metrics, SESSION_FACTORY(), maps, keys_counter_article, ArticleMetric, COLLECTION)
            update_date_metric_status(SESSION_FACTORY(), COLLECTION, f_date, 'status_counter_article_metric', cam_status)

        if 'counter_journal_metric' in target_tables and not params.ignore_counter_metric_tables:
            logging.info('Adicionando métricas agregadas para counter_journal...')
            keys_counter_journal = ['idjournal_cjm', 'idlanguage_cjm', 'idformat_cjm', 'yop', 'year_month_day']
            cjm_status = persist_metrics(r5_metrics, SESSION_FACTORY(), maps, keys_counter_journal, JournalMetric, COLLECTION)
            update_date_metric_status(SESSION_FACTORY(), COLLECTION, f_date, 'status_counter_journal_metric', cjm_status)

        if 'sushi_journal_yop_metric' in target_tables:
            logging.info('Adicionando métricas agregadas para sushi_journal_yop...')
            keys_sushi_journal_yop = ['idjournal_sjym', 'yop', 'year_month_day']
            sjym_status = persist_metrics(r5_metrics, SESSION_FACTORY(), maps, keys_sushi_journal_yop, SushiJournalYOPMetric, COLLECTION)
            update_date_metric_status(SESSION_FACTORY(), COLLECTION, f_date, 'status_sushi_journal_yop_metric', sjym_status)

        if 'sushi_journal_metric' in target_tables:
            logging.info('Adicionando métricas agregadas para sushi_journal...')
            keys_sushi_journal = ['idjournal_sjm', 'year_month_day']
            sjm_status = persist_metrics(r5_metrics, SESSION_FACTORY(), maps, keys_sushi_journal, SushiJournalMetric, COLLECTION)
            update_date_metric_status(SESSION_FACTORY(), COLLECTION, f_date, 'status_sushi_journal_metric', sjm_status)

        if 'sushi_article_metric' in target_tables:
            logging.info('Adicionando métricas agregadas para sushi_article...')
            keys_sushi_article = ['idarticle_sam', 'year_month_day']
            sam_status = persist_metrics(r5_metrics, SESSION_FACTORY(), maps, keys_sushi_article, SushiArticleMetric, COLLECTION)
            update_date_metric_status(SESSION_FACTORY(), COLLECTION, f_date, 'status_sushi_article_metric', sam_status)

        date_status_value = compute_date_metric_status(SESSION_FACTORY(),
                                                       COLLECTION,
                                                       f_date)

        if date_status_value == DATE_STATUS_COMPLETED:
            logging.info('Atualizando tabela control_date_status para %s' % f_date)
            update_date_status(SESSION_FACTORY(),
                               COLLECTION,
                               f_date,
                               DATE_STATUS_COMPLETED)
        else:
            logging.info('Data %s ainda contém agregações a serem calculadas' % f_date)

        logging.info('Tempo total: %.2f segundos' % (time.time() - time_start))
