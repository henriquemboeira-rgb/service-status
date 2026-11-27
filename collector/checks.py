"""Verificações de status.

Cada check recebe a definição do serviço (um item de "services" no config.yaml),
o timeout e uma sessão HTTP, e devolve uma tupla (status, detalhe).
Status possíveis: ok, degraded, down, unknown.
"""

import socket
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import sefaz_portal
from estados import pior

# Campos obrigatórios por tipo de check, validados ao carregar o config.yaml
CAMPOS_OBRIGATORIOS = {
    "statuspage": ["url"],
    "sefaz_portal": ["autorizador"],
}

# Statuspage (Atlassian): status de cada componente e indicador geral da página
MAPA_COMPONENTE = {
    "operational": "ok",
    "under_maintenance": "degraded",
    "degraded_performance": "degraded",
    "partial_outage": "degraded",
    "major_outage": "down",
}
MAPA_INDICADOR = {
    "none": "ok",
    "minor": "degraded",
    "maintenance": "degraded",
    "major": "down",
    "critical": "down",
}


def sessao_resiliente():
    """Sessão HTTP que tenta de novo até 2 vezes em falha de conexão ou 502/503/504."""
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=(502, 503, 504),
        allowed_methods=("GET", "POST"),
        raise_on_status=False,  # devolve a resposta para validarmos o status_code
    )
    sessao = requests.Session()
    sessao.mount("http://", HTTPAdapter(max_retries=retry))
    sessao.mount("https://", HTTPAdapter(max_retries=retry))
    sessao.headers["User-Agent"] = "service-status/0.1"
    return sessao


def check_statuspage(servico, timeout, sessao):
    """Lê o summary.json de uma status page e avalia componentes ou o indicador geral.

    Falha ao acessar a status page vira unknown.
    """
    try:
        resposta = sessao.get(servico["url"], timeout=timeout)
    except requests.RequestException as erro:
        return "unknown", f"status page inacessível ({type(erro).__name__})"

    if resposta.status_code != 200:
        return "unknown", f"status page respondeu HTTP {resposta.status_code}"

    try:
        dados = resposta.json()
    except ValueError:
        return "unknown", "resposta da status page não é JSON"

    filtros = servico.get("components") or []

    # Sem filtro: usa o indicador geral da página
    if not filtros:
        geral = dados.get("status", {})
        indicador = geral.get("indicator")
        return MAPA_INDICADOR.get(indicador, "unknown"), geral.get("description") or f"indicador '{indicador}'"

    # Com filtro: só os componentes cujo nome contém algum dos trechos (grupos são ignorados)
    componentes = [
        c for c in dados.get("components", [])
        if not c.get("group") and any(f.lower() in c.get("name", "").lower() for f in filtros)
    ]
    if not componentes:
        return "unknown", "nenhum componente corresponde ao filtro"

    status = pior(MAPA_COMPONENTE.get(c.get("status"), "unknown") for c in componentes)
    problemas = [f"{c['name']}: {c['status']}" for c in componentes if c.get("status") != "operational"]
    detalhe = "; ".join(problemas) if problemas else f"{len(componentes)} componente(s) operacional(is)"

    nao_encontrados = [f for f in filtros if not any(f.lower() in c["name"].lower() for c in componentes)]
    if nao_encontrados:
        detalhe += f" (filtro sem correspondência: {', '.join(nao_encontrados)})"
    return status, detalhe


def check_tcp(servico, timeout, sessao):
    """Abre uma conexão TCP com host:porta e mede o tempo do handshake. Usado pela sonda (probe)."""
    host, porta = servico["host"], int(servico["port"])
    inicio = time.monotonic()
    try:
        with socket.create_connection((host, porta), timeout=timeout):
            pass
    except TimeoutError:
        return "down", f"timeout após {timeout:g}s"
    except OSError as erro:
        return "down", f"falha na conexão: {erro.strerror or erro}"
    return "ok", f"porta {porta} aberta em {(time.monotonic() - inicio) * 1000:.0f} ms"


CHECKS = {
    "statuspage": check_statuspage,
    "sefaz_portal": sefaz_portal.check_sefaz_portal,
}


def executar(servico, timeout):
    """Executa o check do tipo do serviço e, se houver, a sonda TCP (probe).

    O probe mede a conexão real a partir desta rede. O resultado final é o pior
    entre o check principal e a sonda: por exemplo, status page verde com a
    conexão falhando resulta em vermelho.
    """
    funcao = CHECKS.get(servico["type"])
    if funcao is None:
        return "unknown", f"tipo de check desconhecido: {servico['type']}"
    with sessao_resiliente() as sessao:
        status, detalhe = funcao(servico, timeout, sessao)

    sonda = servico.get("probe")
    if sonda:
        status_sonda, detalhe_sonda = check_tcp(sonda, timeout, None)
        status = pior([status, status_sonda])
        detalhe = f"{detalhe} | conexão: {detalhe_sonda}"
    return status, detalhe
