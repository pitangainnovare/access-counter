from utils import dicts


class CounterStat:
    """
    Modelo de dados utilizado para representar as métricas COUNTER R5
    """
    def __init__(self):
        self.metrics = {'article': {},
                        'issue': {},
                        'journal': {},
                        'platform': {},
                        'others': {}}

    def _get_total(self, hits: list, hit_type, content_type_list):
        """
        Obtém o número total de acessos nos moldes COUNTER R5.

        :param hits: lista de hits
        :param hit_type: tipo de hit a ser considerado (article, issue, journal, platform)
        :content_type_list: lista que contém content_types a serem considerados
        :return: número total e acessos
        """
        return sum([1 for x in hits if x.hit_type == hit_type and x.content_type in content_type_list])

    def _get_unique(self, hits: list, hit_type, content_type_list):
        """
        Obtém o número de acessos únicos nos moldes COUNTER R5.

        :param hits: lista de hits
        :param hit_type: tipo de hit a ser considerado (article, issue, journal, platform)
        :content_type_list: lista que contém content_types a serem considerados
        :return: número de acessos únicos
        """
        valid_hits = [h for h in hits if h.hit_type == hit_type and h.content_type in content_type_list]
        return 1 if valid_hits else 0

    def _ensure_metric_bucket(self, key, ymd, target):
        if key not in target:
            target[key] = {}

        if ymd not in target[key]:
            target[key][ymd] = dicts.counter_item_metrics.copy()

    def _get_article_unique_identity(self, key):
        """
        Define a identidade de item único para artigos.

        O formato fica fora dessa identidade para que HTML/PDF/XML do mesmo artigo
        não multipliquem as métricas unique dentro da mesma sessão.
        """
        pid, _, lang, latitude, longitude, yop = key
        return pid, lang, latitude, longitude, yop

    def _calculate_totals(self, datefied_hits: dict, key, target: dict, group: str):
        group_hit_type = dicts.group_to_hit_type[group]
        group_item_requests = dicts.group_to_item_requests[group]
        group_item_investigations = dicts.group_to_item_investigations[group]

        for ymd in datefied_hits:
            self._ensure_metric_bucket(key, ymd, target)

            target[key][ymd]['total_item_requests'] += self._get_total(
                datefied_hits[ymd],
                group_hit_type,
                group_item_requests)

            target[key][ymd]['total_item_investigations'] += self._get_total(
                datefied_hits[ymd],
                group_hit_type,
                group_item_investigations)

            # Remove valores nulos
            if sum(target[key][ymd].values()) == 0:
                del target[key][ymd]

    def _calculate_uniques_for_non_article(self, datefied_hits: dict, key, target: dict, group: str):
        group_hit_type = dicts.group_to_hit_type[group]
        group_item_requests = dicts.group_to_item_requests[group]
        group_item_investigations = dicts.group_to_item_investigations[group]

        for ymd in datefied_hits:
            self._ensure_metric_bucket(key, ymd, target)

            target[key][ymd]['unique_item_requests'] += self._get_unique(
                datefied_hits[ymd],
                group_hit_type,
                group_item_requests)

            target[key][ymd]['unique_item_investigations'] += self._get_unique(
                datefied_hits[ymd],
                group_hit_type,
                group_item_investigations)

            # Remove valores nulos
            if sum(target[key][ymd].values()) == 0:
                del target[key][ymd]

    def _calculate_article_uniques(self, session_key_hits: dict, target: dict):
        """
        Calcula uniques de artigo uma vez por item/sessão/dia, distribuindo o crédito
        para uma chave canônica entre os formatos observados.
        """
        article_requests = dicts.group_to_item_requests['article']
        article_investigations = dicts.group_to_item_investigations['article']
        families = {}

        for key, hits in session_key_hits.items():
            datefied_hits = self.get_datefied_hits(hits)

            for ymd, ymd_hits in datefied_hits.items():
                self._ensure_metric_bucket(key, ymd, target)

                family_key = (ymd, self._get_article_unique_identity(key))
                earliest_hit_time = min(hit.server_time for hit in ymd_hits)
                family = families.setdefault(family_key, {
                    'request_candidates': [],
                    'investigation_candidates': [],
                })

                if self._get_unique(ymd_hits, dicts.group_to_hit_type['article'], article_requests):
                    family['request_candidates'].append((earliest_hit_time, key))

                if self._get_unique(ymd_hits, dicts.group_to_hit_type['article'], article_investigations):
                    family['investigation_candidates'].append((earliest_hit_time, key))

        for (ymd, _identity), candidates in families.items():
            if candidates['request_candidates']:
                _, canonical_key = min(candidates['request_candidates'], key=lambda item: (item[0], item[1]))
                self._ensure_metric_bucket(canonical_key, ymd, target)
                target[canonical_key][ymd]['unique_item_requests'] += 1

            if candidates['investigation_candidates']:
                _, canonical_key = min(candidates['investigation_candidates'], key=lambda item: (item[0], item[1]))
                self._ensure_metric_bucket(canonical_key, ymd, target)
                target[canonical_key][ymd]['unique_item_investigations'] += 1

    def calculate_metrics(self, data_content):
        """
        Calcula métricas COUNTER e armazena os resultados no campo self.metrics[group: {}]

        @param data_content: dicionário com o conteúdo os dados para cálculo
        """
        for group in data_content.keys():
            for session_id, key_hits in data_content[group].items():
                for key, hits in key_hits.items():
                    datefied_hits = self.get_datefied_hits(hits)
                    self._calculate_totals(datefied_hits, key, self.metrics[group], group)

                    if group != 'article':
                        self._calculate_uniques_for_non_article(datefied_hits, key, self.metrics[group], group)

                if group == 'article':
                    self._calculate_article_uniques(key_hits, self.metrics[group])

    def get_datefied_hits(self, hits):
        """
        Obtém os hits organizados por data (ANO -> MESES -> DIAS)

        :param hits: lista de objetos Hit
        :return: dicionário de hits do tipo YYYY -> {M1, M2, ... -> {D1, D2, ... -> [Hit1, Hit2, ...]}}
        """
        date_to_hits = {}

        for hit in hits:
            year = hit.server_time.year
            month = hit.server_time.month
            day = hit.server_time.day

            year_month_day = '-'.join([str(year), str(month).zfill(2), str(day).zfill(2)])

            if year_month_day not in date_to_hits:
                date_to_hits[year_month_day] = []
            date_to_hits[year_month_day].append(hit)

        return date_to_hits
