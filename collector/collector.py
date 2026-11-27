"""Coletor do Service Status.

Executado pelo timer do systemd: roda os checks em paralelo, grava o status.json
que a extensão do GNOME lê e envia alerta ao Telegram quando algum status muda.
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

import checks
import notify
from estados import pior

APP = "service-status"
log = logging.getLogger(APP)


def dir_xdg(variavel, padrao):
    return Path(os.environ.get(variavel) or Path.home() / padrao) / APP


CONFIG_DIR = dir_xdg("XDG_CONFIG_HOME", ".config")
CACHE_DIR = dir_xdg("XDG_CACHE_HOME", ".cache")

ARQUIVO_CONFIG = CONFIG_DIR / "config.yaml"
ARQUIVO_ENV = CONFIG_DIR / ".env"
ARQUIVO_STATUS = CACHE_DIR / "status.json"


def agora():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ler_json(caminho):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def gravar_json(caminho, dados):
    """Grava num arquivo temporário e renomeia, para a extensão nunca ler um JSON pela metade."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, caminho)


def carregar_config(caminho):
    with open(caminho, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    servicos = config.get("services")
    if not isinstance(servicos, list) or not servicos:
        raise ValueError("o config precisa de uma lista 'services' não vazia")

    ids = set()
    for servico in servicos:
        for campo in ("id", "name", "type"):
            if campo not in servico:
                raise ValueError(f"serviço sem o campo '{campo}': {servico}")
        if servico["id"] in ids:
            raise ValueError(f"id repetido: {servico['id']}")
        ids.add(servico["id"])
        for campo in checks.CAMPOS_OBRIGATORIOS.get(servico["type"], []):
            if campo not in servico:
                raise ValueError(f"serviço '{servico['id']}' ({servico['type']}) sem o campo '{campo}'")
        sonda = servico.get("probe")
        if sonda is not None and not (isinstance(sonda, dict) and "host" in sonda and "port" in sonda):
            raise ValueError(f"serviço '{servico['id']}': probe precisa de host e port")

    # Serviços com enabled: false ficam fora da execução
    config["services"] = [s for s in servicos if s.get("enabled", True)]
    return config


def pode_reaproveitar(servico, anterior):
    intervalo = servico.get("min_interval_seconds")
    if not intervalo or not anterior:
        return False
    try:
        idade = time.time() - datetime.fromisoformat(anterior["checked_at"]).timestamp()
    except (KeyError, ValueError):
        return False
    return idade < intervalo


def verificar(servico, timeout, anterior):
    if pode_reaproveitar(servico, anterior):
        return anterior

    inicio = time.monotonic()
    try:
        status, detalhe = checks.executar(servico, timeout)
    except Exception as erro:  # um check com bug não pode derrubar os demais
        log.exception("erro inesperado no check %s", servico["id"])
        status, detalhe = "unknown", f"erro inesperado no check ({type(erro).__name__})"

    return {
        "id": servico["id"],
        "name": servico["name"],
        "status": status,
        "detail": detalhe,
        "elapsed_ms": round((time.monotonic() - inicio) * 1000),
        "checked_at": agora(),
    }


def notificar_mudancas(resultados, caminho_notificado, timeout):
    """Compara com o último estado notificado e alerta o que mudou.

    O estado notificado só é atualizado depois de um envio bem-sucedido, então
    um alerta que falhou é tentado de novo na próxima execução.
    """
    atual = {r["id"]: r["status"] for r in resultados}
    notificado = ler_json(caminho_notificado)

    if notificado is None:  # primeira execução: só registra a referência
        gravar_json(caminho_notificado, atual)
        return

    mudancas = [
        (r, notificado[r["id"]])
        for r in resultados
        if r["id"] in notificado and notificado[r["id"]] != r["status"]
    ]
    if not mudancas:
        gravar_json(caminho_notificado, atual)  # registra serviços novos ou removidos
        return

    for servico, anterior in mudancas:
        log.info("mudança: %s %s -> %s", servico["name"], anterior, servico["status"])

    if notify.enviar_telegram(notify.montar_mensagem(mudancas), timeout) is not False:
        gravar_json(caminho_notificado, atual)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_dotenv(ARQUIVO_ENV)

    try:
        config = carregar_config(ARQUIVO_CONFIG)
    except (OSError, yaml.YAMLError, ValueError) as erro:
        log.error("configuração inválida: %s", erro)
        # Mesmo com erro, publica um estado para a extensão mostrar cinza com o motivo
        gravar_json(ARQUIVO_STATUS, {
            "generated_at": agora(), "interval_seconds": 60, "overall": "unknown",
            "error": f"configuração inválida: {erro}", "services": [],
        })
        return 1

    timeout = float(config.get("timeout_seconds", 8))
    anteriores = {s["id"]: s for s in (ler_json(ARQUIVO_STATUS) or {}).get("services", [])}

    # Checks em paralelo: um serviço lento não atrasa os outros
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(
            lambda s: verificar(s, timeout, anteriores.get(s["id"])), config["services"]
        ))

    for r in resultados:
        log.info("%-18s %-9s %s", r["name"], r["status"], r["detail"])

    gravar_json(ARQUIVO_STATUS, {
        "generated_at": agora(),
        "interval_seconds": int(config.get("interval_seconds", 60)),
        "overall": pior(r["status"] for r in resultados),
        "services": resultados,
    })
    notificar_mudancas(resultados, ARQUIVO_STATUS.with_name("notificado.json"), timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
