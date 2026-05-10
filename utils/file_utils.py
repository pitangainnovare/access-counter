import json
import os
import pickle
import re


def generate_file_path(directory, name, version, extension):
    filename = f'{name}-{version}{extension}'
    return os.path.join(directory, filename)
    

def load_dictionaries(directory, version):
    dictionaries = {
        'article-codes': {},
        'pid-dates': {},
        'issn-acronym': {},
        'pdf-pid': {},
        'pid-format-lang': {},
        'pid-issn': {}
    }

    dicts_names = os.listdir(directory)

    for d in dicts_names:
        for name in dictionaries.keys():
            if name in d and version in d:
                dictionaries[name] = pickle.load(open(os.path.join(directory, d), 'rb'))

    return dictionaries


def discover_files(directory, prefix):
    files = []

    for jf in [f for f in os.listdir(directory) if f.endswith('.json')]:
        if re.match(prefix, jf):
            files.append(os.path.join(directory, jf))

    return files


def load_data_from_opac_files(files):
    opac_data = {'nbr': {}}

    for f in sorted(files):
        with open(f) as fin:
            fj = json.load(fin)
            collection = fj.get('collection', 'nbr')

            for pid, values in fj.get('documents', {}).items():
                values['collection'] = values.get('collection') or collection
                opac_data['nbr'][pid] = values

    return opac_data


def load_data_from_preprint_files(files):
    preprint_data = {'pre': {}}

    for file in sorted(files):
        with open(file) as fin:
            preprint_metadata = json.load(fin)

            for pid, values in preprint_metadata.items():
                preprint_data['pre'][pid] = values

    return preprint_data


def load_data_from_articlemeta_files(articlemeta_files):
    am_data = {}

    for file in sorted(articlemeta_files):
        with open(file) as fin:
            am_metadata = json.load(fin)

            for doc in am_metadata.get('objects'):
                collection = doc['collection']
                pid = doc['code']

                if collection and collection not in am_data:
                    am_data[collection] = {}

                am_data[collection][pid] = doc

    return am_data


def save(data, filepath):
    with open(filepath, 'wb') as fout:
        pickle.dump(data, fout)
