"""Envio de alertas para o Telegram quando algum serviço muda de status."""

import html
import logging
import os

import requests

log = logging.getLogger("service-status")

EMOJI = {"ok": "🟢", "degraded": "🟡", "down": "🔴", "unknown": "⚪"}
ROTULO = {"ok": "normal", "degraded": "degradado", "down": "fora do ar", "unknown": "indeterminado"}


def montar_mensagem(mudancas):
    """mudancas: lista de (servico_atual, status_anterior). Devolve o texto em HTML do Telegram."""
    linhas = ["<b>Service Status</b>"]
    for servico, anterior in mudancas:
        linhas.append(
            f"{EMOJI[servico['status']]} <b>{html.escape(servico['name'])}</b>: "
            f"{ROTULO[anterior]} → {ROTULO[servico['status']]}\n"
            f"<i>{html.escape(servico['detail'])}</i>"
        )
    return "\n\n".join(linhas)


def enviar_telegram(texto, timeout):
    """Devolve True se enviou, False se falhou e None se o Telegram não está configurado."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("Telegram não configurado no .env; alerta não enviado")
        return None

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resposta = requests.post(
            url,
            json={"chat_id": chat_id, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=timeout,
        )
    except requests.RequestException as erro:
        # Só o tipo do erro vai para o log: a mensagem da exceção contém a URL, que contém o token
        log.error("falha ao enviar alerta ao Telegram (%s)", type(erro).__name__)
        return False

    if resposta.status_code != 200:
        try:
            descricao = resposta.json().get("description", "")
        except ValueError:
            descricao = ""
        log.error("Telegram respondeu HTTP %s %s", resposta.status_code, descricao)
        return False
    return True
