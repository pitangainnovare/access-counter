import argparse
import json
import logging
import os
import re

from datetime import datetime
from scielo_scholarly_data import standardizer
from utils.regular_expressions import REGEX_PREPRINT_PID_PREFIX
from sickle import Sickle


DIR_DATA = os.environ.get(
    'DIR_DATA', 
    '/opt/counter/data'
)

DIR_DICTIONARIES = os.path.join(
    DIR_DATA, 
    'dictionaries'
)

OAI_PMH_PREPRINT_ENDPOINT = os.environ.get(
    'OAI_PMH_PREPRINT_ENDPOINT', 
    'https://preprints.scielo.org/index.php/scielo/oai'
)

OAI_METADATA_PREFIX = os.environ.get(
    'OAI_METADATA_PREFIX', 
    'oai_dc'
)

PREPRINT_DICTIONARY_PREFIX = os.environ.get(
    'PREPRINT_DICTIONARY_PREFIX', 
    'pre-counter-dict'
)


def _extract_doi(identifiers):
    for i in identifiers:
        doi = standardizer.document_doi(i, return_mode='path')
        if 'error' not in doi:
            return doi


def parse(record):
    preprint_pid = re.match(REGEX_PREPRINT_PID_PREFIX, record.header.identifier).group(1)

    return {
        preprint_pid: {
            'publication_date': record.metadata.get('date').pop(),
            'default_language': record.metadata.get('language').pop(),
            'doi': _extract_doi(record.metadata.get('identifier', [])),
            'identifiers': ';'.join([i for i in record.metadata.get('identifier', [])]),
        }
    }


def save(data, filename):
    filepath = os.path.join(DIR_DICTIONARIES, filename)

    if not os.path.exists(DIR_DICTIONARIES):
        os.makedirs(DIR_DICTIONARIES)

    try:
        json.dump(data, open(filepath, 'w'))
    except FileExistsError:
        logging.error('Arquivo %s já existe' % filepath)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--from_date', required=True)
    params = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='[%(asctime)s] %(levelname)s %(message)s',
                        datefmt='%d/%b/%Y %H:%M:%S')

    oai_client = Sickle(endpoint=OAI_PMH_PREPRINT_ENDPOINT, max_retries=3, verify=False)
    records = oai_client.ListRecords(**{
        'metadataPrefix': OAI_METADATA_PREFIX,
        'from': params.from_date
    })

    logging.info('Obtendo dados do OAI-PMH Preprints para (%s,%s)' % (params.from_date, params.until_date))
    data = {}
    for r in records:
        data.update(parse(r))

    filename = f'{PREPRINT_DICTIONARY_PREFIX}-{params.from_date}.json'
    save(data, filename)
