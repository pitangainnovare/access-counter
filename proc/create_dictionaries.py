import argparse
import datetime
import logging
import os

from utils import dict_utils, dict_validator, file_utils


DIR_DATA = os.environ.get(
    'DIR_DATA', 
    '/app/data'
)

LOGGING_LEVEL = os.environ.get(
    'LOGGING_LEVEL', 
    'INFO'
)

DIR_DICTIONARIES = os.path.join(
    DIR_DATA, 
    'dictionaries'
)

ARTICLEMETA_PREFIX_PREFIX = os.environ.get(
    'ARTICLEMETA_PREFIX_PREFIX', 
    r'^am-counter-dict'
)

OPAC_DICTIONARY_PREFIX = os.environ.get(
    'OPAC_DICTIONARY_PREFIX', 
    r'^opac-counter-dict'
)

PREPRINT_DICTIONARY_PREFIX = os.environ.get(
    'PREPRINT_DICTIONARY_PREFIX', 
    r'^pre-counter-dict'
)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--current_version_date',
        help='Data da versão atual dos dicionáros (YYYY-MM-DD)'
    )

    parser.add_argument(
        '--new_version_date',
        default=datetime.datetime.now().strftime('%Y-%m-%d'),
        help='Data da nova versão dos dicionários (YYYY-MM-DD)'
    )

    parser.add_argument(
        '--logging_level',
        choices=['CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET'],
        dest='logging_level',
        default=LOGGING_LEVEL
    )

    params = parser.parse_args()

    logging.basicConfig(level=LOGGING_LEVEL,
                        format='[%(asctime)s] %(levelname)s %(message)s',
                        datefmt='%d/%b/%Y %H:%M:%S')

    if not os.path.exists(DIR_DICTIONARIES):
        os.makedirs(DIR_DICTIONARIES)

    logging.info('Carregando dados de dicionários atuais')
    current_dicts = file_utils.load_dictionaries(DIR_DICTIONARIES, params.current_version_date)

    logging.info('Carregando dados de OPAC')
    opac_data = file_utils.load_data_from_opac_files(file_utils.discover_files(DIR_DICTIONARIES, OPAC_DICTIONARY_PREFIX))
    dict_utils.add_info_to_article_codes_from_opac(opac_data, current_dicts['article-codes'])
    dict_utils.add_info_to_dates_dict(opac_data, current_dicts['pid-dates'])
    dict_utils.add_info_to_pid_format_lang(opac_data, current_dicts['pid-format-lang'])
    del opac_data

    logging.info('Carregando dados de Preprints')
    preprint_data = file_utils.load_data_from_preprint_files(file_utils.discover_files(DIR_DICTIONARIES, PREPRINT_DICTIONARY_PREFIX))
    dict_utils.add_info_to_article_codes_from_preprints(preprint_data, current_dicts['article-codes'])
    dict_utils.add_info_to_dates_dict(preprint_data, current_dicts['pid-dates'])
    dict_utils.add_info_to_pid_format_lang(preprint_data, current_dicts['pid-format-lang'])
    del preprint_data

    logging.info('Carregando dados de Articlemeta')
    am_data = file_utils.load_data_from_articlemeta_files(file_utils.discover_files(DIR_DICTIONARIES, ARTICLEMETA_PREFIX_PREFIX))
    dict_utils.add_info_to_article_codes_from_articlemeta(am_data, current_dicts['article-codes'])
    dict_utils.update_dicts_with_am_counter_dict(current_dicts, am_data)
    del am_data

    logging.info('Removendo dados inválidos')
    dict_validator.clean(current_dicts)

    for d_name in [
        'article-codes',
        'pid-issn', 
        'pid-format-lang',
        'pdf-pid',
        'issn-acronym',
        'pid-dates',
    ]:
        new_dict_path = file_utils.generate_file_path(DIR_DICTIONARIES, d_name, params.new_version_date, '.data')
        logging.info(f'Gravando dicionário {d_name} em {new_dict_path}')
        file_utils.save(current_dicts[d_name], new_dict_path)
