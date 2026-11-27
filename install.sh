#!/usr/bin/env bash
# Instala o coletor (venv + systemd --user) e a extensão do GNOME para o usuário atual.
set -euo pipefail

APP="service-status"
UUID="service-status@henrique"
REPO="$(cd "$(dirname "$0")" && pwd)"
SHARE="$HOME/.local/share/$APP"
CONF="$HOME/.config/$APP"
UNITS="$HOME/.config/systemd/user"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"

for cmd in python3 systemctl gnome-extensions; do
    command -v "$cmd" >/dev/null || { echo "Erro: '$cmd' não encontrado." >&2; exit 1; }
done

echo "==> Coletor em $SHARE"
mkdir -p "$SHARE" "$CONF" "$UNITS"
rm -rf "$SHARE/collector"
cp -r "$REPO/collector" "$SHARE/collector"
[ -d "$SHARE/venv" ] || python3 -m venv "$SHARE/venv"
"$SHARE/venv/bin/pip" install --quiet --upgrade -r "$REPO/requirements.txt"

echo "==> Configuração em $CONF (arquivos existentes são preservados)"
[ -f "$CONF/config.yaml" ] || cp "$REPO/config.example.yaml" "$CONF/config.yaml"
if [ ! -f "$CONF/.env" ]; then
    install -m 600 "$REPO/.env.example" "$CONF/.env"
    echo "    Edite $CONF/.env com o token do Telegram."
fi

echo "==> Timer do systemd"
cp "$REPO/systemd/$APP.service" "$REPO/systemd/$APP.timer" "$UNITS/"
systemctl --user daemon-reload
systemctl --user enable --now "$APP.timer"
# Primeira execução imediata: gera o status.json e dá referência ao OnUnitActiveSec
systemctl --user start "$APP.service" || echo "    Aviso: veja 'journalctl --user -u $APP'"

echo "==> Extensão em $EXT_DIR"
mkdir -p "$(dirname "$EXT_DIR")"   # em sistemas novos a pasta de extensões ainda não existe
rm -rf "$EXT_DIR"
cp -r "$REPO/extension/$UUID" "$EXT_DIR"
if gnome-extensions enable "$UUID" 2>/dev/null; then
    echo "    Extensão ativada."
else
    echo "    No Wayland o GNOME Shell só enxerga extensões novas após novo login."
    echo "    Depois de entrar de novo, rode: gnome-extensions enable $UUID"
fi

echo "Pronto. Logs do coletor: journalctl --user -u $APP -f"
