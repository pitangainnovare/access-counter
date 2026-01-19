import logging

from datetime import datetime
from utils import map_actions as at
from utils import values
from libs import lib_hit, lib_counter
from urllib.parse import urljoin


class Hit:
    """
    Classe que representa o acesso a uma página (ação)
    """
    def __init__(self, **kargs):
        # Endereço IP
        self.ip = kargs.get('ip', '')

        # Localização associada ao IP
        self.latitude = kargs.get('latitude', '')
        self.longitude = kargs.get('longitude', '')

        # Data e horário do acesso
        if isinstance(kargs.get('serverTime', ''), datetime):
            self.server_time = kargs.get('serverTime')
        else:
            self.server_time = datetime.strptime(kargs.get('serverTime', ''), '%Y-%m-%d %H:%M:%S')

        # Nome do navegador utilizado
        self.browser_name = kargs.get('browserName', '').lower()

        # Versão do navegador utilizado
        self.browser_version = kargs.get('browserVersion', '').lower()

        # Domínio acessado
        self.domain = kargs.get('domain', '')
        
        # URL da ação
        self.action_name = kargs.get('actionName', '')

        self._set_domain_to_action_name(self.domain, self.action_name)

        # Um boleano que indica se o Hit é válido
        self.valid = True

    def _set_domain_to_action_name(self, domain, action_name):
        self.action_name = urljoin(domain, action_name)

    def _is_from_local_network(self):
        if not self.latitude or self.latitude.lower() in {'', 'null'}:
            return True
        if not self.longitude or self.longitude.lower() in {'', 'null'}:
            return True
        return False

    def _is_null_action(self):
        if not self.action_name or self.action_name.lower() in {'', 'null'}:
            return True
        return False

    def is_valid_hit(self):
        # Verifica se Hit é da rede local
        if self._is_from_local_network():
            logging.debug('Hit ignorado. Acesso é da rede local (IP: %s, ActionName: %s)' % (self.ip, self.action_name))
            return False

        # Verifica se Hit é relacionado a ação nula
        if self._is_null_action():
            logging.debug('Hit ignorado. Atributo action_name está vazio (IP: %s, ActionName: %s)' % (self.ip, self.action_name))
            return False

        return True

    def is_trackable_hit(self):
        # Ignora Hits não artigo
        if self.hit_type != at.HIT_TYPE_ARTICLE:
            return False

        # Verifica se Hit possui conteúdo indefinido
        if self.content_type == at.HIT_CONTENT_OTHERS:
            logging.debug('Hit ignorado. Conteúdo não é rastreável (HitType: %s, ActionName: %s)' % (self.hit_type, self.action_name))
            return False

        # Verifica se Hit possui tipo indefinido
        if self.hit_type == at.HIT_CONTENT_OTHERS:
            logging.debug('Hit ignorado. Tipo não foi determinado (IP: %s, ActionName: %s)' % (self.ip, self.action_name))
            return False

        if not self.issn and self.hit_type in {at.HIT_TYPE_ARTICLE,
                                               at.HIT_TYPE_ISSUE,
                                               at.HIT_TYPE_JOURNAL}:
            logging.debug('Hit ignorado. ISSN não está definido (HitType: %s, ActionName: %s' % (self.hit_type, self.action_name))
            return False

        # Verifica se Hit possui um PID válido
        if self.hit_type == at.HIT_TYPE_ARTICLE and not self.pid:
            logging.debug('Hit ignorado. PID não está definido (HitType: %s, ActionName: %s' % (self.hit_type, self.action_name))
            return False

        # Verifica se Hit foi invalidado
        if not self.valid:
            logging.warning('Hit (%s) é inválido' % self.action_name)
            return False

        return True

    def has_valid_language(self):
        if len(self.lang) == 2 or len(self.lang) == 3:
            if self.lang.isalpha():
                return True
        return False

    def has_valid_format(self):
        if self.format in values.VALID_FORMATS:
            return True
        return False


class HitManager:
    """
    Classe que gerencia objetos Hit
    """
    def __init__(self, path_pdf_to_pid, issn_to_acronym, pid_to_format_lang, pid_to_yop):
        self.hits = {'article': {}, 'issue': {}, 'journal': {}, 'platform': {}, 'others': {}}

        # Dicionários para tratamento de PID
        self.pdf_path_to_pid = path_pdf_to_pid
        self.issn_to_acronym = issn_to_acronym
        self.pid_to_format_lang = pid_to_format_lang
        self.pid_to_yop = pid_to_yop
        self.pid_to_issn = {}

        # Gera um dicionário reverso de acrônimos
        self.acronym_to_issn = self._generate_acronym_to_issn()

    def _generate_acronym_to_issn(self):
        """
        Obtém um dicionário Acrônimo:ISSN a partir de ISSN:Acrônimo
        """
        new_dict = {}
        for col, key_value in self.issn_to_acronym.items():
            if col not in new_dict:
                new_dict[col] = {}

            for k, v in key_value.items():
                if v not in new_dict[col]:
                    new_dict[col][v] = []

                if k not in new_dict[col][v]:
                    new_dict[col][v].append(k)
        return new_dict

    def create_hit(self, row, default_collection, domain):
        """
        Cria objeto Hit

        @param row: um objeto LogLinkVisitAction
        @param mode: possui o valor `file` se crição é a partir de arquivo ou `sql` se é a partir de base de dados
        @param default_collection: coleção
        @return: um objeto Hit
        """
        row.update({'domain': domain})
        new_hit = Hit(**row)

        if new_hit.is_valid_hit():
            # Caso Hit tenha um conteúdo válido, Obtém outros atributos
            self.set_hit_attrs(new_hit, default_collection)

            # Caso Hit seja rastreável (associável a um Periódico, Fascículo ou Artigo)
            if new_hit.is_trackable_hit():
                return new_hit

        # Caso Hit seja ou inválido ou não rastreável
        return

    def set_hit_attrs(self, hit, default_collection):
        """
        Seta os atributos de um Hit usando dados do Hit Manager

        @hit: um objeto Hit
        """
        # Gera um ID de sessão
        hit.session_id = lib_counter.generate_session_id(hit.ip,
                                                         hit.browser_name,
                                                         hit.browser_version,
                                                         hit.server_time)

        # Obtém coleção ao qual o Hit pertence
        hit.collection = default_collection

        if hit.collection == 'pre':
            self._set_hit_attrs_preprint_url(hit)
        elif hit.collection == 'ssp':
            self._set_hit_attrs_ssp_url(hit)
        else:
            if lib_hit.is_new_url_format(hit.action_name.lower()):
                self._set_hit_attrs_new_url(hit)
            else:
                self._set_hit_attrs_classic_url(hit)

    def _set_hit_attrs_new_url(self, hit):
        hit.action_params = lib_hit.get_url_params_from_action_new_url(hit.action_name)

        hit.pid = hit.action_params.get('pid', '')
        hit.acronym = hit.action_params.get('acronym', '').lower()
        hit.format = hit.action_params.get('format', '').lower()
        hit.lang = hit.action_params.get('lang', '').lower()

        if hit.action_params.get('resource_ssm_path'):
            hit.action_params.update(lib_hit.get_attrs_from_ssm_path(hit.action_params.get('resource_ssm_path', '')))
            hit.issn = hit.action_params.get('issn', '').upper()
            hit.format = hit.action_params.get('format', '').lower()
            hit.pid = hit.action_params.get('pid', '')

        if not hit.has_valid_format():
            hit.valid = False

        if hit.pid not in self.pid_to_format_lang.get(hit.collection, {}):
            hit.valid = False

        hit.content_type = lib_hit.get_content_type_new_url(hit)
        hit.hit_type = lib_hit.get_hit_type_new_url(hit.action_name.lower())

        if hit.hit_type == at.HIT_TYPE_ARTICLE:
            # Dicionário de acrônimos não contém coleção nbr - os dados são idênticos ao da coleção scl
            collection_to_check = 'scl' if hit.collection == 'nbr' else hit.collection

            if 'issn' not in hit.__dict__.keys() or not hit.issn:
                hit.issn = self.acronym_to_issn.get(collection_to_check, {}).get(hit.acronym, [''])[0].upper()

            if hit.pid not in self.pid_to_issn:
                if hit.issn:
                    self.pid_to_issn[hit.pid] = {hit.issn}
            else:
                self.pid_to_issn[hit.pid].add(hit.issn)
                if len(self.pid_to_issn[hit.pid]) > 2:
                    logging.warning('PID %s está associado a mais de dois ISSNs: %s' % (hit.pid, self.pid_to_issn[hit.pid]))

            hit.yop = lib_hit.get_year_of_publication_new_url(hit, self.pid_to_yop)
            if not hit.lang or not hit.has_valid_language():
                hit.lang = lib_hit.get_language_new_url(hit, self.pid_to_format_lang)

    def _set_hit_attrs_classic_url(self, hit):
        hit.action_name = hit.action_name.lower()

        # Extrai parâmetros da URL de ação de um Hit
        hit.action_params = lib_hit.get_url_params_from_action(hit.action_name)

        # Obtém dados a partir dos parâmetros extraídos (relacionados ao formato de URL clássica)
        hit.pid = hit.action_params.get('pid', '').upper()
        hit.lang = hit.action_params.get('tlng', '')
        hit.script = hit.action_params.get('script', '')
        hit.issn = hit.action_params.get('issn', '').upper()

        # Obtém PID do Hit, caso parâmetro de URL não tenha conseguido obtê-lo
        if not hit.pid:
            hit.pid = lib_hit.get_pid_from_pdf_path(hit, self.pdf_path_to_pid)

        # Obtém o tipo de conteúdo associado ao Hit
        hit.content_type = lib_hit.get_content_type(hit)

        # Obtém o formato do Hit (PDF ou HTML)
        hit.format = lib_hit.get_format(hit)

        # Obtém o tipo de Hit
        hit.hit_type = lib_hit.get_hit_type(hit)

        hit.issn = lib_hit.article_pid_to_journal_issn(hit.pid, self.pid_to_issn)
        hit.acronym = lib_hit.get_journal_acronym(hit, self.issn_to_acronym)
        if not hit.issn:
            hit.issn = lib_hit.get_issn(hit, self.acronym_to_issn)

        if hit.hit_type == at.HIT_TYPE_ARTICLE:
            hit.yop = lib_hit.get_year_of_publication(hit, self.pid_to_yop)
            hit.lang = lib_hit.get_language(hit, self.pid_to_format_lang)

    def _set_hit_attrs_preprint_url(self, hit):
        hit.issn = values.GENERIC_ISSN
        hit.pid = lib_hit.get_pid_preprint(hit)

        if hit.pid:
            hit.hit_type = lib_hit.ma.HIT_TYPE_ARTICLE
            if hit.pid not in self.pid_to_issn:
                self.pid_to_issn[hit.pid] = {hit.issn}
        else:
            hit.hit_type = lib_hit.ma.HIT_TYPE_OTHERS

        hit.content_type = lib_hit.get_content_type_preprints(hit)
        hit.format = lib_hit.get_format_preprints(hit)
        hit.lang = lib_hit.get_language_preprints(hit, self.pid_to_format_lang)
        hit.yop = lib_hit.get_year_of_publication_preprints(hit, self.pid_to_yop)

    def _set_hit_attrs_ssp_url(self, hit):
        hit.action_params = lib_hit.get_url_params_from_action_ssp_url(hit.action_name)

        hit.acronym = hit.action_params.get('acronym', '').lower()
        hit.format = hit.action_params.get('format', '').lower()
        hit.lang = hit.action_params.get('lang', '').lower()
        hit.content_type = lib_hit.get_content_type_ssp_url(hit)
        hit.hit_type = lib_hit.get_hit_type_ssp(hit.action_name.lower())

        if hit.hit_type == at.HIT_TYPE_ARTICLE:
            hit.pid = lib_hit.get_ssp_pid(hit.action_params)
            hit.issn = self.acronym_to_issn.get('spa', {}).get(hit.acronym, [''])[0].upper()

            if hit.pid not in self.pid_to_issn:
                if hit.issn:
                    self.pid_to_issn[hit.pid] = {hit.issn}
            else:
                self.pid_to_issn[hit.pid].add(hit.issn)
                if len(self.pid_to_issn[hit.pid]) > 2:
                    logging.warning('PID %s está associado a mais de dois ISSNs: %s' %(hit.pid, self.pid_to_issn[hit.pid]))

            hit.yop = lib_hit.get_year_of_publication_ssp_pid(hit.pid)
            if not hit.lang or not hit.has_valid_language():
                hit.lang = lib_hit.get_language_ssp(hit.pid, self.pid_to_format_lang)

    def reset(self):
        """
        Limpa registros do HitManager
        """
        self.hits = {'article': {}, 'issue': {}, 'journal': {}, 'platform': {}, 'others': {}}

    def add_hit(self, hit: Hit):
        if hit.hit_type == at.HIT_TYPE_ARTICLE:
            key = (hit.pid, hit.format, hit.lang, hit.latitude, hit.longitude, hit.yop)
            group = 'article'
        elif hit.hit_type == at.HIT_TYPE_ISSUE:
            issue_code = hit.pid
            key = (hit.issn, issue_code, hit.latitude, hit.longitude)
            group = 'issue'
        elif hit.hit_type == at.HIT_TYPE_JOURNAL:
            key = (hit.issn, hit.latitude, hit.longitude)
            group = 'journal'
        elif hit.hit_type == at.HIT_TYPE_PLATFORM:
            key = ('scielo', hit.latitude, hit.longitude)
            group = 'platform'
        else:
            key = ('others', hit.latitude, hit.longitude)
            group = 'others'

        if hit.session_id not in self.hits[group]:
            self.hits[group][hit.session_id] = {}

        if key not in self.hits[group][hit.session_id]:
            self.hits[group][hit.session_id][key] = []

        self.hits[group][hit.session_id][key].append(hit)

    def remove_double_clicks(self):
        """
        Remove cliques duplos. São comparados os hits dentro de uma sessão.
        """
        for group, session_key_hits in self.hits.items():
            for session, key_hits in session_key_hits.items():
                for key, hits in key_hits.items():
                    cleaned_hits = []

                    if len(hits) > 1:
                        sorted_hits = sorted(hits, key=lambda x: x.server_time)

                        for i in range(len(sorted_hits) - 1):
                            past_hit = sorted_hits[i]
                            current_hit = sorted_hits[i + 1]

                            if not lib_counter.is_double_click(group, past_hit, current_hit):
                                cleaned_hits.append(past_hit)
                                if i + 2 == len(sorted_hits):
                                    cleaned_hits.append(current_hit)
                            elif i + 2 == len(sorted_hits):
                                cleaned_hits.append(current_hit)
                    else:
                        cleaned_hits.extend(hits)

                    key_hits[key] = cleaned_hits
