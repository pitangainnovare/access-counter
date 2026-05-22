import logging
from re import I
import langcodes

from scielo_scholarly_data import standardizer


def _is_valid_pid(collection, pid):
    if collection != 'pre':
        if len(pid) == 23:
            return True
        return False

    if pid.isdigit():
        return True

    return False


def _is_valid_acronym(acronym):
    if not len(acronym) > 0:
        return False

    return True


def _is_valid_path(path):
    if not len(path) > 0:
        return False

    return True


def _has_something(data):
    if len(data) <= 0:
        return False

    return True


def _extract_langs(data):
    keys = ['pdf', 'html']
    langs = set()

    for key in keys:
        for lang in data.get(key, set()):
            langs.add(lang)

    default_lang = data.get('default')
    if default_lang:
        langs.add(default_lang)

    return langs


def is_pid_dates_ok(data):
    is_ok = True

    for collection in data:
        for pid in data[collection]:
            if not _is_valid_pid(collection, pid):
                logging.warning('Dicionário tem problemas de PID para (%s,%s)' % (collection, pid))
                is_ok = False

        if not _has_something(data[collection]):
            logging.warning('Dicionário não tem dados para (%s)' % collection)
            is_ok = False

    return is_ok


def is_pid_format_lang_ok(data):
    is_ok = True

    for collection in data:
        for pid in data[collection]:
            if not _is_valid_pid(collection, pid):
                logging.warning('Dicionário tem problemas de PID para (%s,%s)' % (collection, pid))
                is_ok = False

            for lang in _extract_langs(data[collection][pid]):
                if not langcodes.tag_is_valid(lang):
                    logging.warning('Dicionário tem problemas de Language para (%s,%s,%s)' % (collection, pid, lang))
                    is_ok = False

        if not _has_something(data[collection]):
            logging.warning('Dicionário não tem dados para (%s)' % collection)
            is_ok = False

    return is_ok


def is_pid_issn_ok(data):
    is_ok = True

    for collection in data:
        for pid in data[collection]:
            if not _is_valid_pid(collection, pid):
                logging.warning('Dicionário tem problemas de PID para (%s,%s)' % (collection, pid))
                is_ok = False

            for issn in data[collection][pid]:
                if not standardizer.journal_issn(issn):
                    logging.warning('Dicionário tem problemas de ISSN para (%s,%s,%s)' % (collection, pid, issn))
                    is_ok = False

        if not _has_something(data[collection]):
            logging.warning('Dicionário não tem dados para (%s)' % collection)
            is_ok = False

    return is_ok


def is_issn_acronym_ok(data):
    is_ok = True

    for collection in data:
        for issn in data[collection]:
            if not standardizer.journal_issn(issn):
                logging.warning('Dicionário tem problemas de ISSN para (%s,%s)' % (collection, issn))
                is_ok = False

            if not _is_valid_acronym(data[collection][issn]):
                logging.warning('Dicionário tem problemas de Acrônimo para (%s,%s,%s)' % (collection, issn, data[collection][issn]))

        if not _has_something(data[collection]):
            logging.warning('Dicionário não tem dados para (%s)' % collection)
            is_ok = False

    return is_ok


def is_pdf_pid_ok(data):
    is_ok = True

    for collection in data:
        for path in data[collection]:
            for pid in data[collection][path]:
                if not _is_valid_pid(collection, pid):
                    logging.warning('Dicionário tem problemas de PID para (%s,%s,%s)' % (collection, path, pid))
                    is_ok = False

            if not _is_valid_path(path):
                logging.warning('Dicionário tem problemas de PDF para (%s,%s,%s)' % (collection, path))
                is_ok = False

        if not _has_something(data[collection]):
            logging.warning('Dicionário não tem dados para (%s)' % collection)
            is_ok = False

    return is_ok


def clean_pid_issn(data):
    items_to_remove = set()

    for collection in data:
        for pid in data[collection]:
            if not _is_valid_pid(collection, pid):
                items_to_remove.add((collection, pid))
                continue

            std_issns = []
            for issn in data[collection][pid]:
                std_issn = standardizer.journal_issn(issn)
                if std_issn:
                    std_issns.append(std_issn)

            logging.debug(f'Padronizando ({collection},{pid},{data[collection][pid]}) para {collection},{pid},{std_issns}')
            data[collection][pid] = std_issns

    for i in items_to_remove:
        collection, pid = i
        logging.warning(f'Removendo registro ({collection},{pid}) de pid-issn')
        del data[collection][pid]


def clean_issn_acronym(data):
    items_to_remove = set()

    for collection in data:
        for issn in data[collection]:
            std_issn = standardizer.journal_issn(issn)

            if std_issn and std_issn != issn:
                logging.warning(f'Padronizando ({collection},{issn}) para {collection},{std_issn}')
                data[collection][std_issn] = data[collection][issn]
                items_to_remove.add((collection, issn))

    for i in items_to_remove:
        collection, issn = i
        logging.warning(f'Removendo registro ({collection},{issn}) de issn-acronym')
        del data[collection][issn]


def _standardize_langcode(language):
    try:
        if langcodes.tag_is_valid(language):
            return langcodes.standardize_tag(language)
    except AttributeError as e:
        logging.error(f"Erro ao padronizar {language}: {e}")

    try:
        inferred_lang, score = langcodes.best_match(language, langcodes.LANGUAGE_ALPHA3.keys())
    except langcodes.tag_parser.LanguageTagError:
        return

    if score >= 0.75:
        return langcodes.standardize_tag(inferred_lang)


def clean_pid_format_lang(data):
    items_to_remove = set()
    items_to_add = set()

    for collection in data:
        for pid in data[collection]:
            for key, value in data[collection][pid].items():
                if key == 'default':
                    std_value = _standardize_langcode(value)
                    if not std_value:
                        logging.warning(f'Removendo {collection},{pid},{key},{value}')
                        items_to_remove.add((collection, pid, key))
                        
                    if std_value and std_value != value:
                        logging.warning(f'Corrigindo {value} para {std_value} em {collection},{pid},{key}')
                        data[collection][pid][key] = std_value

                elif isinstance(value, set):
                    for lang in value:
                        std_lang = _standardize_langcode(lang)

                        if not std_lang:
                            items_to_remove.add((collection, pid, key, lang))

                        if std_lang and std_lang != lang:
                            logging.warning(f'Removendo {collection},{pid},{key},{lang}')
                            items_to_remove.add((collection, pid, key, lang))

                            logging.warning(f'Adicionando {collection},{pid},{key},{std_lang}')
                            items_to_add.add((collection, pid, key, std_lang))

    for i in items_to_remove:
        if len(i) == 3:
            collection, pid, key = i
            del data[collection][pid][key]

        if len(i) == 4:
            collection, pid, key, lang = i
            data[collection][pid][key].remove(lang)

    for i in items_to_add:
        collection, pid, key, lang = i
        data[collection][pid][key].add(i)


def clean(data):
    clean_pid_issn(data['pid-issn'])
    clean_issn_acronym(data['issn-acronym'])
    clean_pid_format_lang(data['pid-format-lang'])
