"""Status da NF-e pelo painel público do Portal Nacional.

Como é scraping, qualquer mudança no layout vira unknown (cinza), nunca ok.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from estados import pior

URL_PADRAO = "https://www.nfe.fazenda.gov.br/portal/disponibilidade.aspx"
ID_TABELA = "ctl00_ContentPlaceHolder1_gdvDisponibilidade2"
FUSO_PORTAL = ZoneInfo("America/Sao_Paulo")

# Trecho do nome da imagem (ex.: imagens/bola_verde_P.png) -> status
CORES = {"verde": "ok", "amarel": "degraded", "vermelh": "down"}
ROTULO_COR = {"ok": "verde", "degraded": "amarelo", "down": "vermelho", "unknown": "desconhecido"}


def normalizar(texto):
    """Remove espaços extras e quebras de linha, para comparar nomes de colunas."""
    return " ".join(texto.split())


def cor_da_celula(td):
    """Status da bolinha da célula, ou None se a célula não tem imagem."""
    img = td.find("img")
    if img is None:
        return None
    src = img.get("src", "").lower()
    for trecho, status in CORES.items():
        if trecho in src:
            return status
    return "unknown"


def localizar_tabela(soup):
    tabela = soup.find(id=ID_TABELA)
    if tabela is not None:
        return tabela
    # Plano B se o id mudar: a tabela cujo cabeçalho tem "Autorizador"
    for candidata in soup.find_all("table"):
        if candidata.find("th", string=re.compile("autorizador", re.I)):
            return candidata
    return None


def interpretar(html):
    """Transforma o HTML em dados estruturados.

    Devolve (ultima_verificacao, linhas), em que linhas é
    {autorizador: {"cores": {coluna: status}, "textos": {coluna: texto}}}.
    Levanta ValueError se não reconhecer a estrutura da página.
    """
    soup = BeautifulSoup(html, "html.parser")
    tabela = localizar_tabela(soup)
    if tabela is None:
        raise ValueError("tabela de disponibilidade não encontrada")

    cabecalho = None
    linhas = {}
    for tr in tabela.find_all("tr"):
        ths = tr.find_all("th")
        if ths and cabecalho is None:
            cabecalho = [normalizar(th.get_text(" ")) for th in ths]
            continue

        tds = tr.find_all("td")
        if not tds or cabecalho is None:
            continue

        autorizador = normalizar(tds[0].get_text()).upper()
        cores, textos = {}, {}
        for coluna, td in zip(cabecalho[1:], tds[1:]):
            cor = cor_da_celula(td)
            if cor is not None:
                cores[coluna] = cor
            else:
                textos[coluna] = normalizar(td.get_text())
        linhas[autorizador] = {"cores": cores, "textos": textos}

    if cabecalho is None or not linhas:
        raise ValueError("tabela sem cabeçalho ou sem linhas")

    # A legenda da tabela traz a data da última verificação feita pela SEFAZ
    ultima_verificacao = None
    legenda = tabela.find("caption")
    if legenda is not None:
        achado = re.search(r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})", legenda.get_text())
        if achado:
            ultima_verificacao = datetime.strptime(achado.group(1), "%d/%m/%Y %H:%M:%S").replace(tzinfo=FUSO_PORTAL)

    return ultima_verificacao, linhas


def avaliar(linhas, ultima_verificacao, servico, agora=None):
    autorizador = str(servico["autorizador"]).upper()
    linha = linhas.get(autorizador)
    if linha is None:
        return "unknown", f"autorizador {autorizador} não aparece no painel"

    cores = linha["cores"]
    filtro = servico.get("colunas")
    if filtro:
        por_nome = {c.casefold(): c for c in cores}
        faltando = [c for c in filtro if c.casefold() not in por_nome]
        if faltando:
            return "unknown", f"coluna(s) não encontrada(s) no painel: {', '.join(faltando)}"
        cores = {por_nome[c.casefold()]: cores[por_nome[c.casefold()]] for c in filtro}

    if not cores:
        return "unknown", f"{autorizador} sem indicadores no painel"

    status = pior(cores.values())
    problemas = [f"{coluna} {ROTULO_COR[cor]}" for coluna, cor in cores.items() if cor != "ok"]
    detalhe = f"{autorizador}: " + ("; ".join(problemas) if problemas else f"{len(cores)} serviço(s) normais")

    tempo_medio = next((t for c, t in linha["textos"].items() if "tempo" in c.lower()), "")
    if tempo_medio and tempo_medio != "-":
        detalhe += f", tempo médio {tempo_medio}"

    if ultima_verificacao is not None:
        agora = agora or datetime.now(FUSO_PORTAL)
        limite = timedelta(minutes=int(servico.get("max_idade_painel_min", 30)))
        if agora - ultima_verificacao > limite:
            return "unknown", f"{detalhe} (painel sem atualização desde {ultima_verificacao:%d/%m %H:%M})"
        detalhe += f" (painel das {ultima_verificacao:%H:%M})"

    return status, detalhe


def check_sefaz_portal(servico, timeout, sessao):
    try:
        resposta = sessao.get(servico.get("url", URL_PADRAO), timeout=timeout)
    except requests.exceptions.SSLError:
        return "unknown", "falha TLS ao acessar o portal"
    except requests.RequestException as erro:
        return "unknown", f"portal inacessível ({type(erro).__name__})"

    if resposta.status_code != 200:
        return "unknown", f"portal respondeu HTTP {resposta.status_code}"

    try:
        ultima_verificacao, linhas = interpretar(resposta.content)
    except ValueError as erro:
        return "unknown", f"layout do portal não reconhecido: {erro}"

    return avaliar(linhas, ultima_verificacao, servico)
