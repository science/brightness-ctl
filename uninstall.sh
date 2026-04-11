#!/usr/bin/env bash
#
# uninstall.sh - Remove brightness-ctl symlink and systemd user service.
# Leaves ~/.config/brightness-ctl/ (config, state, luminance logs) in place.
#

set -euo pipefail

INSTALL_BIN="$HOME/.local/bin/brightness-ctl"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/brightness-ctl.service"

if systemctl --user list-unit-files brightness-ctl.service &>/dev/null; then
    if systemctl --user is-active --quiet brightness-ctl.service; then
        echo "Stopping brightness-ctl service..."
        systemctl --user stop brightness-ctl.service || true
    fi
    if systemctl --user is-enabled --quiet brightness-ctl.service; then
        echo "Disabling brightness-ctl service..."
        systemctl --user disable brightness-ctl.service || true
    fi
fi

if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    echo "Removed: $SERVICE_FILE"
fi

systemctl --user daemon-reload

if [[ -L "$INSTALL_BIN" || -f "$INSTALL_BIN" ]]; then
    rm -f "$INSTALL_BIN"
    echo "Removed: $INSTALL_BIN"
fi

echo "Done. Config and luminance logs left at ~/.config/brightness-ctl/"
