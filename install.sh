#!/usr/bin/env bash
#
# install.sh - Install brightness-ctl symlink and systemd user service
# Following the audio-switcher install pattern.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_BIN="$HOME/.local/bin/brightness-ctl"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/brightness-ctl.service"
CONFIG_DIR="$HOME/.config/brightness-ctl"
OLD_CONFIG_DIR="$HOME/.config/redshift-ctl"

# Migrate config dir from old name if needed
if [[ -d "$OLD_CONFIG_DIR" && ! -d "$CONFIG_DIR" ]]; then
    echo "Migrating config: $OLD_CONFIG_DIR -> $CONFIG_DIR"
    mv "$OLD_CONFIG_DIR" "$CONFIG_DIR"
fi

mkdir -p "$HOME/.local/bin"
mkdir -p "$CONFIG_DIR"

# Stop old daemon if running
OLD_PID_FILE="/tmp/redshift-ctl-daemon.pid"
NEW_PID_FILE="/tmp/brightness-ctl-daemon.pid"
for pidfile in "$OLD_PID_FILE" "$NEW_PID_FILE"; do
    if [[ -f "$pidfile" ]]; then
        old_pid=$(cat "$pidfile")
        if kill -0 "$old_pid" 2>/dev/null; then
            echo "Stopping old daemon (PID $old_pid)"
            kill "$old_pid" || true
        fi
        rm -f "$pidfile" 2>/dev/null || true
    fi
done

# Remove old symlink/script if it points elsewhere or is not a symlink
OLD_BIN="$HOME/.local/bin/redshift-ctl"
if [[ -f "$OLD_BIN" && ! -L "$OLD_BIN" ]]; then
    echo "Removing old script: $OLD_BIN"
    rm -f "$OLD_BIN"
elif [[ -L "$OLD_BIN" ]]; then
    echo "Removing old symlink: $OLD_BIN"
    rm -f "$OLD_BIN"
fi

# Create symlink
ln -sf "$SCRIPT_DIR/src/brightness-ctl" "$INSTALL_BIN"
echo "Symlinked: $INSTALL_BIN -> $SCRIPT_DIR/src/brightness-ctl"

echo "Done. brightness-ctl is now available at $INSTALL_BIN"
