from datetime import datetime


def _extract_user_agent(browser_name: str, browser_version: str):
    """
    Obtém o agente de usuário com base nas informações de nome e versão de navegador

    :param browser_name: nome do navegador
    :param browser_version: versão do navegador
    :return: agente do usuário
    """
    return '/'.join([browser_name, browser_version])


def _extract_slice(date: datetime):
    """
    Extrai uma fatia no formato YYYY-MM-DD|H

    :param date: um elemento datetime
    :return: uma str no formato YYYY-MM-DD|H
    """
    str_date = '-'.join([str(x) for x in [date.year, date.month, date.day]])
    str_hour_slice = str(date.hour)
    return '|'.join([str_date, str_hour_slice])


def generate_session_id(ip: str, browser_name: str, browser_version: str, date: datetime):
    """
    Gera um ID de sessão

    :param ip: endereço IP
    :param browser_name: nome do navegador
    :param browser_version: versão do navegador
    :param date: data e hora
    :return: uma str que representa um ID de sessão
    """
    date_slice = _extract_slice(date)
    user_agent = _extract_user_agent(browser_name, browser_version)
    return '|'.join([ip, user_agent, date_slice])


def _get_double_click_signature(group, hit):
    """
    Monta uma assinatura canônica para comparação de double-click.

    Para artigos, removemos o `format` da assinatura para evitar que a lógica
    de duplicidade dependa da forma de entrega do item.
    """
    if group == 'article':
        return (
            getattr(hit, 'pid', ''),
            getattr(hit, 'content_type', ''),
            getattr(hit, 'lang', ''),
        )

    if group == 'issue' or group == 'journal':
        return (
            getattr(hit, 'issn', ''),
            getattr(hit, 'pid', ''),
            getattr(hit, 'content_type', ''),
        )

    return (
        getattr(hit, 'content_type', ''),
        getattr(hit, 'action_name', ''),
    )


def is_double_click(group, past_hit, current_hit):
    """
    Verifica se a ação atual é um duplo-clique usando uma assinatura canônica em até 30 segundos.

    :param group: grupo de Hits (article | issue | journal | platform | others)
    :param past_hit: ação mais antiga
    :param current_hit: ação atual
    :return: True se for duplo-clique, False caso contrário
    """
    time_delta = current_hit.server_time - past_hit.server_time

    if time_delta.total_seconds() > 30:
        return False

    return _get_double_click_signature(group, past_hit) == _get_double_click_signature(group, current_hit)
