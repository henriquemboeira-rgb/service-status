#!/usr/bin/env bash
# Remove coletor, timer e extensão. A configuração em ~/.config/service-status é mantida.
set -uo pipefail

APP="service-status"
UUID="service-status@henrique"

gnome-extensions disable "$UUID" 2>/dev/null
rm -rf "$HOME/.local/share/gnome-shell/extensions/$UUID"
systemctl --user disable --now "$APP.timer" 2>/dev/null
rm -f "$HOME/.config/systemd/user/$APP.service" "$HOME/.config/systemd/user/$APP.timer"
systemctl --user daemon-reload
rm -rf "$HOME/.local/share/$APP" "$HOME/.cache/$APP"
echo "Removido. Para apagar a configuração: rm -rf ~/.config/$APP"
