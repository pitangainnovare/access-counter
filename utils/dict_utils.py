import langcodes
import logging
import re

from dateutil import parser as date_parser
from scielo_scholarly_data import standardizer
from utils.regular_expressions import REGEX_YEAR


def _clean_value(value):
    if value is None:
        return ''
    return str(value).strip()


def _normalize_article_code_collection(collection):
    collection = _clean_value(collection).lower()
    if collection == 'nbr':
        return 'scl'
    return collection


def _extract_doi_from_values(values):
    candidates = []

    for key in ('doi', 'DOI'):
        candidates.append(values.get(key))

    for pdf in values.get('pdfs') or []:
        candidates.append(pdf.get('doi'))

    article = values.get('article') or {}
    for item in article.get('v237') or []:
        candidates.append(item.get('_'))

    for identifier in values.get('identifiers', '').split(';'):
        candidates.append(identifier)

    for candidate in candidates:
        if not candidate:
            continue
        doi = standardizer.document_doi(candidate, return_mode='path')
        if doi and 'error' not in doi:
            return doi

    return ''


def _ensure_article_code_collection(article_codes, collection):
    if collection not in article_codes:
        article_codes[collection] = {}


def _merge_article_code(article_codes, collection, key, pid_v2='', pid_v3='', doi=''):
    collection = _normalize_article_code_collection(collection)
    key = _clean_value(key)
    pid_v2 = _clean_value(pid_v2).upper()
    pid_v3 = _clean_value(pid_v3)
    doi = _clean_value(doi)

    if not collection or not key:
        return

    _ensure_article_code_collection(article_codes, collection)

    current = article_codes[collection].setdefault(key, {
        'collection': collection,
        'pid_v2': '',
        'pid_v3': '',
        'doi': '',
    })

    for field, value in (
        ('pid_v2', pid_v2),
        ('pid_v3', pid_v3),
        ('doi', doi),
    ):
        old_value = current.get(field, '')
        if value and old_value and old_value != value:
            logging.warning(
                '%s de %s-%s mudou de %s para %s',
                field,
                collection,
                key,
                old_value,
                value,
            )
        if value:
            current[field] = value


def add_info_to_article_codes_from_opac(data, article_codes):
    for _, pids in data.items():
        for pid, values in pids.items():
            collection = _normalize_article_code_collection(values.get('collection', 'scl'))
            pid_v2 = values.get('pid_v2') or values.get('pid') or ''
            pid_v3 = values.get('pid_v3') or pid
            doi = _extract_doi_from_values(values)

            _merge_article_code(article_codes, collection, pid_v3 or pid_v2, pid_v2, pid_v3, doi)


def add_info_to_article_codes_from_preprints(data, article_codes):
    for collection, pids in data.items():
        for pid, values in pids.items():
            doi = _extract_doi_from_values(values)
            _merge_article_code(article_codes, collection, pid, '', pid, doi)


def add_info_to_article_codes_from_articlemeta(data, article_codes):
    for collection, pids in data.items():
        for pid, values in pids.items():
            doi = _extract_doi_from_values(values)
            _merge_article_code(article_codes, collection, pid, pid, values.get('pid_v3', ''), doi)


def _update_pid_issn(current_dict, collection, pid, issn):
    if pid not in current_dict[collection]:
        current_dict[collection][pid] = []

    if issn not in current_dict[collection][pid]:
        current_dict[collection][pid].append(issn)


def _update_issn_acronym(current_dict, collection, issn, journal_acronym):
    old_acronym = current_dict.get(collection, {}).get(issn)
    if old_acronym:
        if old_acronym != journal_acronym:
            logging.warning(f'acronym de {collection}-{issn} mudou de {old_acronym} mudou para {journal_acronym}')

    current_dict[collection][issn] = journal_acronym


def _are_similar_dates(date1, date2):
    try:
        date1 = date_parser.parse(date1).strftime('%Y-%m-%d')
    except date_parser._parser.ParserError:
        ...

    try:
        date2 = date_parser.parse(date2).strftime('%Y-%m-%d')
    except date_parser._parser.ParserError:
        ...

    if date1 == date2:
        return True
    
    return False


def _check_for_date_change(current_dict, collection, pid, date_name, date_dict):
    old_date_value = current_dict.get(collection, {}).get(pid, {}).get(date_name)
    new_date_value = date_dict[date_name]

    if old_date_value and not _are_similar_dates(old_date_value, new_date_value):
        logging.warning(f'{date_name} de {collection}-{pid} mudou de {old_date_value} para {new_date_value}')


def _check_for_lang_change(current_dict, collection, pid, format_lang_dict):
    old_lang_values = current_dict.get(collection, {}).get(pid, {})
    
    if old_lang_values:
        old_lang_default = old_lang_values.get('default')
        old_langs_html = old_lang_values.get('html')
        old_langs_pdf = old_lang_values.get('pdf')

        new_lang_default = format_lang_dict.get('default')
        new_langs_html = format_lang_dict.get('html')
        if new_langs_html:
            new_langs_html = set(new_langs_html)

        new_langs_pdf = format_lang_dict.get('pdf')
        if new_langs_pdf:
            new_langs_pdf = set(new_langs_pdf)

        if new_lang_default and old_lang_default:
            if old_lang_default != new_lang_default:
                logging.warning(f'default de {collection}-{pid} mudou de {old_lang_default} para {new_lang_default}')

        if new_langs_html and old_langs_html:
            if old_langs_html != new_langs_html:
                logging.warning(f'html de {collection}-{pid} mudou de {old_langs_html} para {new_langs_html}')

        if new_langs_pdf and old_langs_pdf:
            if old_langs_pdf != new_langs_pdf:
                logging.warning(f'pdf de {collection}-{pid} mudou de {old_langs_pdf} para {new_langs_pdf}')


def _update_pid_dates(current_dict, collection, pid, date_dict):
    if pid not in current_dict[collection]:
        current_dict[collection][pid] = {}

    for date_name in date_dict:
        if date_name != 'updated_at':
            _check_for_date_change(current_dict, collection, pid, date_name, date_dict)

    current_dict[collection][pid].update(date_dict)


def _update_pid_format_lang(current_dict, collection, pid, format_lang_dict):
    if pid not in current_dict[collection]:
        current_dict[collection][pid] = {}

    _check_for_lang_change(current_dict, collection, pid, format_lang_dict)

    current_dict[collection][pid].update(format_lang_dict)


def _get_date_dict(data):
    dates = {}

    dates['publication_date'] = data.get('publication_date', '')

    pub_year = standardizer.document_publication_date(data.get('publication_year', ''), only_year=True)
    if not pub_year:
        dates['publication_year'] = standardizer.document_publication_date(dates['publication_date'], only_year=True)

    dates['created_at'] = data.get('created_at', '')
    dates['updated_at'] = data.get('updated_at', '')
    dates['processing_date'] = data.get('processing_date', '')

    return dates


def _get_format_lang_dict(data):
    html_langs = set(data.get('text_langs') or [])
    pdf_langs = set()

    
    for pdf in [x for x in data.get('pdfs', [])]:
        pdf_lang = pdf.get('lang')

        if pdf_lang and pdf_lang != '':
            pdf_langs.add(pdf_lang)

    default_lang = data.get('default_language', '')

    return {
        'default': default_lang,
        'html': html_langs,
        'pdf': pdf_langs,
    }


def _get_pdf_path_list(data):
    return ['/' + p.get('path').lower() for p in data.get('pdfs') if p.get('path') is not None]


def _update_pdf_path(current_dict, collection, pid, pdf_path):
    if pdf_path not in current_dict[collection]:
        current_dict[collection][pdf_path] = set()
        logging.warning(f'{collection}-{pdf_path} foi adicionado ao dicionário')

    old_pdfpath_pids = current_dict.get(collection, {}).get(pdf_path) or set()
    if pid not in old_pdfpath_pids:
        logging.warning(f'{collection}-{pdf_path} mudou de {old_pdfpath_pids} para {old_pdfpath_pids.union(set([pid]))}')

    current_dict[collection][pdf_path].add(pid)


def _update_dicts_with_am_counter_dict_doc(current_dicts, document_collection, document_code, data):
    pid = document_code.upper()
    collection = document_collection.lower()

    for d in current_dicts:
        if collection not in current_dicts[d]:
            current_dicts[d][collection] = {}

    for std_issn in [istd for istd in [standardizer.journal_issn(i) for i in data['code_title'] if i is not None] if istd]:
        _update_pid_issn(current_dicts['pid-issn'], collection, pid, std_issn)

        journal_acronym = data.get('journal_acronym', '').lower()
        if journal_acronym:
            _update_issn_acronym(current_dicts['issn-acronym'], collection, std_issn, journal_acronym)

    _update_pid_dates(current_dicts['pid-dates'], collection, pid, _get_date_dict(data))
    _update_pid_format_lang(current_dicts['pid-format-lang'], collection, pid, _get_format_lang_dict(data))

    for pdf_path in _get_pdf_path_list(data):
        _update_pdf_path(current_dicts['pdf-pid'], collection, pid, pdf_path)


def update_dicts_with_am_counter_dict(current_dicts, data):
    for collection in data:
        for pid in data[collection]:
            _update_dicts_with_am_counter_dict_doc(current_dicts, collection, pid, data[collection][pid])


def _put_date(date_str, date_name, data, collection, pid):
    old_date_value = data.get(collection, {}).get(pid, {}).get(date_name)

    try:
        new_date_value = date_parser.parse(date_str).strftime('%Y-%m-%d %H:%M:%S')
        
        if date_name != 'updated_at' and old_date_value and old_date_value != new_date_value:
            logging.warning(f'{date_name} de {collection}-{pid} mudou de {old_date_value} para {new_date_value}')

        data.update({date_name: new_date_value})
    except ValueError:
        ...


def _extract_year(date_str):
    try:
        return date_parser.parse(date_str).strftime('%Y')

    except date_parser.ParserError:
        matches = {''}

        for m in re.findall(REGEX_YEAR, date_str):
            matches.add(m)

        return max(matches)

    except ValueError:
        return ''


def _standardize_langcode(language):
    if langcodes.tag_is_valid(language):
        return langcodes.standardize_tag(language)

    logging.warning(f'Tentando padronizar {language}')
    inferred_lang, score = langcodes.best_match(language, langcodes.LANGUAGE_ALPHA3.keys())
        
    if score >= 0.75:
        return langcodes.standardize_tag(inferred_lang)


def add_info_to_dates_dict(data, dates_dict):
    for collection, pids in data.items():
        if collection not in dates_dict:
            dates_dict[collection] = {}

        for pid, values in data[collection].items():
            if pid not in dates_dict[collection]:
                dates_dict[collection][pid] = {}

            new_publication_date = values.get('publication_date')

            if new_publication_date:
                old_publication_date = dates_dict[collection][pid].get('publication_date')
                if old_publication_date and old_publication_date != new_publication_date:
                    logging.warning(f'publication_date de {collection}-{pid} mudou de {old_publication_date} para {new_publication_date}')

                dates_dict[collection][pid]['publication_date'] = new_publication_date
                new_year = _extract_year(new_publication_date)

                if new_year:
                    old_year = dates_dict[collection][pid].get('publication_year')
                    if old_year and old_year != new_year:
                        logging.warning(f'publication_year de {collection}-{pid} mudou de {old_year} para {new_year}')

                    dates_dict[collection][pid].update({'publication_year': new_year})

            if collection != 'pre':
                _put_date(values.get('create'), 'created_at', dates_dict, collection, pid)
                _put_date(values.get('update'), 'updated_at', dates_dict, collection, pid)


def add_info_to_pid_format_lang(data, pid_format_lang_dict):
    for collection, pids in data.items():
        if collection not in pid_format_lang_dict:
            pid_format_lang_dict[collection] = {}

        for pid, values in pids.items():
            if pid not in pid_format_lang_dict[collection]:
                pid_format_lang_dict[collection][pid] = {}

            default_lang = values.get('default_language', '').lower()

            if default_lang:
                default_lang_std = _standardize_langcode(default_lang)

                old_default_lang = pid_format_lang_dict[collection][pid].get('default')
                if old_default_lang and old_default_lang != default_lang_std:
                    logging.warning(f'default de {collection}-{pid} mudou de {old_default_lang} para {default_lang_std}')

                pid_format_lang_dict[collection][pid].update({'default': default_lang_std})
