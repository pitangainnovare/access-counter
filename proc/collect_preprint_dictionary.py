import argparse
import json
import logging
import os
import re

from collections import Counter
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


def _first(values):
    if isinstance(values, list):
        return values[0] if values else ''

    return values or ''


def parse(record):
    identifier = getattr(getattr(record, 'header', None), 'identifier', '')
    match = re.match(REGEX_PREPRINT_PID_PREFIX, identifier)
    if not match:
        logging.warning('Ignorando registro com identificador inválido: %s', identifier)
        return None, 'skipped_invalid_identifier'

    metadata = getattr(record, 'metadata', None)
    if not metadata:
        logging.warning('Ignorando registro sem metadata: %s', identifier)
        return None, 'skipped_without_metadata'

    preprint_pid = match.group(1)
    identifiers = metadata.get('identifier', [])

    return {
        preprint_pid: {
            'publication_date': _first(metadata.get('date')),
            'default_language': _first(metadata.get('language')),
            'doi': _extract_doi(identifiers),
            'identifiers': ';'.join([i for i in identifiers]),
        }
    }, None


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
        'from': params.from_date,
        'ignore_deleted': True,
    })

    logging.info(f"Obtendo dados do OAI-PMH Preprints para {params.from_date}")
    data = {}
    stats = Counter()
    for r in records:
        stats['processed'] += 1
        parsed, skip_reason = parse(r)
        if parsed:
            data.update(parsed)
            stats['saved'] += len(parsed)
        elif skip_reason:
            stats[skip_reason] += 1

    filename = f'{PREPRINT_DICTIONARY_PREFIX}-{params.from_date}.json'
    save(data, filename)

    for key in sorted(stats):
        logging.info('%s: %s', key, stats[key])
