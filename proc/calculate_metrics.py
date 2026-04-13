import argparse
import csv
import datetime
import logging
import os
import pickle
import re

from libs.lib_database import update_date_status, get_date_status
from libs.lib_status import DATE_STATUS_PRETABLE, DATE_STATUS_COMPUTED
from models.counter import CounterStat
from models.hit import HitManager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from time import time
from libs import lib_hit


MATOMO_DATABASE_STRING = os.environ.get('MATOMO_DATABASE_STRING', 'mysql://user:pass@localhost:3306/matomo')
COLLECTION = os.environ.get('COLLECTION', 'scl')
DIR_DATA = os.environ.get('DIR_DATA', '/app/data')
MATOMO_DB_IP_COUNTER_LIMIT = int(os.environ.get('MATOMO_DB_IP_COUNTER_LIMIT', '100000'))
MATOMO_ID_SITE = os.environ.get('MATOMO_ID_SITE', '1')
MATOMO_URL = os.environ.get('MATOMO_URL', 'http://172.17.0.4')
COMPUTING_TIMEDELTA = int(os.environ.get('COMPUTING_TIMEDELTA', '15'))
COMPUTING_DAYS_N = int(os.environ.get('COMPUTING_DAYS_N', '30'))
MIN_YEAR = int(os.environ.get('MIN_YEAR', '1900'))
LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')

MAX_YEAR = datetime.datetime.now().year + 5
DIR_DICTIONARIES = os.path.join(DIR_DATA, 'dictionaries')
DIR_PRETABLES = os.environ.get('DIR_PRETABLES', os.path.join(DIR_DATA, 'pretables'))
DIR_R5_HITS = os.environ.get('DIR_R5_HITS', os.path.join(DIR_DATA, 'r5/hits'))
DIR_R5_METRICS = os.environ.get('DIR_R5_METRICS', os.path.join(DIR_DATA, 'r5/metrics'))
DIR_R5_LOGS = os.environ.get('DIR_R5_LOGS', os.path.join(DIR_DATA, 'r5/logs'))

ENGINE = create_engine(MATOMO_DATABASE_STRING, pool_recycle=1800)
SESSION_FACTORY = sessionmaker(bind=ENGINE)


def load_dictionaries(dir_dictionaries, date):
    maps = {}
    for d_name in ['pdf-pid', 'issn-acronym', 'pid-format-lang', 'pid-dates']:
        logging.info('Carregando dicionário %s-%s' % (d_name, date))
        d_full_path = os.path.join(dir_dictionaries, d_name + '-' + date + '.data')

        if not os.path.exists(d_full_path):
            logging.error('Não há dicionário %s' % d_full_path)
            exit(1)

        maps[d_name] = pickle.load(open(d_full_path, 'rb'))

    return maps


def get_dates(date: str):
    """
    Obtém lista de dias para extrair informações do Matomo

    @param date: uma data em formato de YYYY-MM-DD ou um período no formato YYYY-MM-DD,YYYY-MM-DD
    @return: lista de data(s) em formato de objeto datetime
    """
    if ',' not in date:
        return [datetime.datetime.strptime(date, '%Y-%m-%d')]
    else:
        dates = date.split(',')
        if len(dates) == 2:
            start = datetime.datetime.strptime(dates[0], '%Y-%m-%d')
            end = datetime.datetime.strptime(dates[1], '%Y-%m-%d') + datetime.timedelta(days=1)
            return [start + datetime.timedelta(days=x) for x in range(0, (end - start).days)]


def _is_valid_for_computing(date_value, date_status, max_permitted_day, all_computed_days_in_dir):
    if date_value in all_computed_days_in_dir:
        logging.warning('Dados de %s já existem. Este dia não será calculado novamente' % date_value)
        return False

    date = datetime.datetime.strptime(date_value, '%Y-%m-%d')

    if date_status == DATE_STATUS_PRETABLE:
        if date <= max_permitted_day:
            return True
        else:
            logging.info('Data %s é recente e exige dicionário mais atualizado' % date_value)
    elif date_status > DATE_STATUS_PRETABLE:
        logging.warning('Data %s já foi computada (status = %d)' % (date_value, date_status))
    else:
        logging.warning('Registro indica não haver pré-tabela para data %s' % date_value)


def _list_files(dir_path):
    results = os.listdir(dir_path)
    return [f for f in results if os.path.isfile(os.path.join(dir_path, f))]


def get_pretables(db_session, collection, max_day: datetime.datetime):
    """
    Obtém lista de caminhos de arquivos log com dados previamente extraídos do Matomo

    @param db_session: sessão de conexão com banco de dados
    @param max_day: dia mais recente a ser computado
    @return: lista de caminhos de arquivo(s) log
    """
    pretables = []
    if os.path.isdir(DIR_PRETABLES):
        pretables.extend([os.path.join(os.path.abspath(DIR_PRETABLES), f) for f in _list_files(DIR_PRETABLES)])

    all_computed_days_in_dir = [get_date_from_file_path(f) for f in set(_list_files(DIR_R5_HITS) + _list_files(DIR_R5_METRICS))]

    pretables_to_compute = []

    for pt in sorted(pretables):
        date_value = get_date_from_file_path(pt)
        date_status = get_date_status(db_session, collection, date_value)
        
        if date_status:
            if _is_valid_for_computing(date_value, date_status, max_day, all_computed_days_in_dir):
                pretables_to_compute.append(pt)
                if len(pretables_to_compute) >= COMPUTING_DAYS_N:
                    break
        else:
            logging.error('Data %s não está registrada na base' % pt)

    return pretables_to_compute


def get_date_from_file_path(file_path: str):
    """
    Obtém uma data a partir de nome de arquivo.
    :param file_path: caminho completo do arquivo
    :return: uma data
    """
    pattern_date = r'(\d{4}-\d{2}-\d{2})\.'
    matched_date = re.search(pattern_date, file_path)
    if matched_date:
        return matched_date.groups()[0]
    else:
        logging.error('Não foi possível detectar data válida em nome de arquivo: %s' % file_path)
        exit(1)


def update_metrics(metric, data):
    """
    Atualiza valores de métricas COUNTER para um registro.
    Caso registro seja novo, considera os valores no objeto data.
    Caso registro já existe, faz soma valores atuais com valores novos

    @param metric: registro de Metric
    @param data: dados a serem adicionados ou atribuídos ao registro
    """
    keys = ['total_item_investigations',
            'total_item_requests',
            'unique_item_investigations',
            'unique_item_requests']

    for k in keys:
        current_value = metric.__getattribute__(k)
        if current_value:
            metric.__setattr__(k, current_value + data[k])
        else:
            metric.__setattr__(k, data[k])


def export_article_hits_to_csv(hits: dict, file_prefix: str, pid_to_issn: dict):
    file_full_path = os.path.join(DIR_R5_HITS, 'r5-hits-' + file_prefix + '.csv')

    with open(file_full_path, 'a') as f:
        for session, hits_data in hits.items():
            for key, hits_list in hits_data.items():
                pid, fmt, lang, lat, long, yop = key
                issn = lib_hit.article_pid_to_journal_issn(pid, pid_to_issn)

                line_data = [pid, fmt, lang, lat, long, yop, issn]

                for hit in hits_list:
                    year = hit.server_time.year
                    month = hit.server_time.month
                    day = hit.server_time.day
                    hour = hit.server_time.hour
                    minute = hit.server_time.minute
                    second = hit.server_time.second

                    ymdhms = '-'.join([str(year),
                                       str(month).zfill(2),
                                       str(day).zfill(2),
                                       str(hour).zfill(2),
                                       str(minute).zfill(2),
                                       str(second).zfill(2)])

                    f.write('|'.join(line_data + [hit.session_id,
                                                  ymdhms,
                                                  hit.action_name]) + '\n')


def export_article_metrics_to_csv(metrics: dict, file_prefix: str, pid_to_issn: dict):
    file_full_path = os.path.join(DIR_R5_METRICS, 'r5-metrics-' + file_prefix + '.csv')

    with open(file_full_path, 'a') as f:
        for key, article_data in metrics.items():
            pid, fmt, lang, lat, long, yop = key
            issn = lib_hit.article_pid_to_journal_issn(pid, pid_to_issn)

            for ymd in article_data:
                line_data = [pid, fmt, lang, lat, long, yop, issn, ymd]
                line_data.append(article_data[ymd]['total_item_investigations'])
                line_data.append(article_data[ymd]['total_item_requests'])
                line_data.append(article_data[ymd]['unique_item_investigations'])
                line_data.append(article_data[ymd]['unique_item_requests'])
                f.write('|'.join([str(i) for i in line_data]) + '\n')


def run(data, hit_manager: HitManager, db_session, collection, result_file_prefix, domain):
    """
    Cria objetos Hit e chama rotinas COUNTER a cada 50 mil (valor definido em BUCKET_LIMIT) iterações.
    Por questões de limitação de memória, o método trabalha por IP.
    Para cada IP, são obtidos os registros a ele relacionados, de tabela pré-extraída da base de dados Matomo

    @param data: arquivo de pré-tabela ou result query
    @param hit_manager: gerenciador de objetos Hit
    @param db_session: sessão com banco de dados
    @param collection: acrônimo de coleção
    @param result_file_prefix: um prefixo para ser usado no nome do arquivo com as métricas e hits
    """
    # IP atual a ser contabilizado
    past_ip = ''

    # Contador para rodar rotinas counter a cada IP_LIMIT
    ip_counter = 0

    # Verificador da primeira linha
    line_counter = 0

    for d in data:
        line_counter += 1
        ip_counter += 1

        current_ip = d.get('ip', '')
        
        if line_counter == 1:
            past_ip = current_ip

        if past_ip != current_ip:
            ip_counter += 1

            if ip_counter >= MATOMO_DB_IP_COUNTER_LIMIT:
                run_counter_routines(hit_manager=hit_manager,
                                     db_session=db_session,
                                     collection=collection,
                                     file_prefix=result_file_prefix)
                ip_counter = 0

            past_ip = current_ip

        hit = hit_manager.create_hit(d, collection, domain)

        if hit:
            hit_manager.add_hit(hit)

    run_counter_routines(hit_manager=hit_manager,
                         db_session=db_session,
                         collection=collection,
                         file_prefix=result_file_prefix)


def run_counter_routines(hit_manager: HitManager, db_session, collection, file_prefix):
    """
    Executa métodos COUNTER para remover cliques-duplos, contar acessos por PID e extrair métricas.
    Ao final, salva resultados (métricas) em base de dados

    @param hit_manager: gerenciador de objetos Hit
    @param db_session: sessão com banco de dados
    @param collection: acrônimo de coleção
    @param file_prefix: um prefixo para ser usado no nome do arquivo com as métricas e hits
    """
    hit_manager.remove_double_clicks()

    cs = CounterStat()
    cs.calculate_metrics(hit_manager.hits)

    logging.info('Salvando hits em disco...')
    export_article_hits_to_csv(hit_manager.hits['article'], file_prefix, hit_manager.pid_to_issn)

    logging.info('Salvando métricas em disco...')
    export_article_metrics_to_csv(cs.metrics['article'], file_prefix, hit_manager.pid_to_issn)

    hit_manager.reset()


def main():
    usage = 'Calcula métricas COUNTER R5 usando dados de acesso SciELO'
    parser = argparse.ArgumentParser(usage)

    parser.add_argument(
        '-c', '--collection',
        dest='collection',
        default=COLLECTION,
        help='Acrônimo da coleção objeto de extração de métricas'
    )

    parser.add_argument(
        '-u', '--matomo_db_uri',
        default=MATOMO_DATABASE_STRING,
        dest='matomo_db_uri',
        help='String de conexão a base SQL Matomo no formato mysql://username:password@host1:port/database'
    )

    parser.add_argument(
        '--logging_level',
        choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET'],
        dest='logging_level',
        default=LOGGING_LEVEL,
        help='Nivel de log'
    )

    parser.add_argument(
        '--dict_date',
        dest='dict_date',
        required=True,
        help='Data, no formato YYYY-MM-DD, da versão dos dicionários a serem utilizados'
    )

    parser.add_argument(
        '--domain',
        required=True,
        help='Domínio do arquivo de log',
    )

    params = parser.parse_args()

    if not os.path.exists(DIR_R5_LOGS):
        os.makedirs(DIR_R5_LOGS)

    collection = params.collection

    file_log_name = collection + '_' + time().__str__() + '.log'
    file_log_path = os.path.join(DIR_R5_LOGS, file_log_name)
    file_log = logging.FileHandler(file_log_path)
    file_log.setLevel(params.logging_level)

    console_log = logging.StreamHandler()
    console_log.setLevel(logging.INFO)

    logging.basicConfig(level=params.logging_level,
                        format='[%(asctime)s] %(levelname)s %(message)s',
                        datefmt='%d/%b/%Y %H:%M:%S',
                        handlers=[file_log, console_log])

    if not os.path.exists(DIR_R5_METRICS):
        os.makedirs(DIR_R5_METRICS)

    if not os.path.exists(DIR_R5_HITS):
        os.makedirs(DIR_R5_HITS)

    maps = load_dictionaries(DIR_DICTIONARIES, params.dict_date)
    
    computing_time_delta = datetime.timedelta(days=COMPUTING_TIMEDELTA)
    max_day_available_for_computing = datetime.datetime.strptime(params.dict_date, '%Y-%m-%d') - computing_time_delta

    hit_manager = HitManager(
        path_pdf_to_pid=maps['pdf-pid'],
        issn_to_acronym=maps['issn-acronym'],
        pid_to_format_lang=maps['pid-format-lang'],
        pid_to_yop=maps['pid-dates'],
    )

    pretables = get_pretables(SESSION_FACTORY(), collection, max_day_available_for_computing)

    logging.info('Há %d pré-tabela(s) para ser(em) computada(s)' % len(pretables))

    for pt in pretables:
        time_start = time()

        logging.info('Extraindo dados do arquivo {}...'.format(pt))
        hit_manager.reset()

        pretable_date_value = get_date_from_file_path(pt)

        with open(pt, errors='ignore') as data:
            csv_data = csv.DictReader(data, delimiter='\t')
            run(data=csv_data,
                hit_manager=hit_manager,
                db_session=SESSION_FACTORY(),
                collection=collection,
                result_file_prefix=pretable_date_value,
                domain=params.domain)

        logging.info('Atualizando tabela control_date_status para %s' % pretable_date_value)
        update_date_status(SESSION_FACTORY(),
                            collection,
                            pretable_date_value,
                            DATE_STATUS_COMPUTED)

        time_end = time()
        logging.info('Durou %.2f segundos' % (time_end - time_start))
