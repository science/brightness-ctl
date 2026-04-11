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

# Migrate bash config to TOML
if [[ -f "$CONFIG_DIR/config" && ! -f "$CONFIG_DIR/config.toml" ]]; then
    echo "Migrating bash config to TOML..."
    python3 -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR/src')
from config import migrate_bash_config
from pathlib import Path
migrate_bash_config(Path('$CONFIG_DIR/config'), Path('$CONFIG_DIR/config.toml'))
"
fi

# Migrate bash state to JSON
if [[ -f "$CONFIG_DIR/state" && ! -f "$CONFIG_DIR/state.json" ]]; then
    echo "Migrating bash state to JSON..."
    python3 -c "
import json
from pathlib import Path
state_file = Path('$CONFIG_DIR/state')
data = {}
for line in state_file.read_text().splitlines():
    if '=' in line:
        k, v = line.split('=', 1)
        if k == 'enabled':
            data[k] = v == '1'
        else:
            try: data[k] = int(v)
            except ValueError: data[k] = v
Path('$CONFIG_DIR/state.json').write_text(json.dumps(data, indent=2) + '\n')
"
fi

# Add explanatory comment to cinnamon-base.dconf for brightness-ctl keybindings
# These keybindings live in cinnamon-base.dconf (applied to all Cinnamon desktops
# including VMs). On VMs, brightness-ctl is not installed so the keybindings are
# harmless no-ops. The host WM intercepts these keys before the VM guest sees them,
# so there is no conflict. The daemon itself is only installed on physical desktops
# (gated by the linux-bambam case in yadm bootstrap).
DCONF_FILE="$HOME/.config/yadm/dconf/cinnamon-base.dconf"
if [[ -f "$DCONF_FILE" ]]; then
    COMMENT="# brightness-ctl keybindings (custom0-4): controls color temperature and"
    if ! grep -qF "$COMMENT" "$DCONF_FILE"; then
        sed -i '/^\[org\/cinnamon\/desktop\/keybindings\/custom-keybindings\/custom0\]/i \
# brightness-ctl keybindings (custom0-4): controls color temperature and\
# hardware/software brightness via ~/dev/brightness-ctl daemon. Only installed\
# on physical desktops (linux-bambam). Harmless no-ops on VMs since the binary\
# is not present and the host WM intercepts these keys before the guest sees them.' "$DCONF_FILE"
        echo "Added brightness-ctl comment to $DCONF_FILE"
    fi
fi

mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Color temperature and brightness daemon
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/brightness-ctl daemon
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0

[Install]
WantedBy=default.target
EOF
echo "Wrote systemd unit: $SERVICE_FILE"

systemctl --user daemon-reload
systemctl --user enable brightness-ctl.service >/dev/null
if systemctl --user is-active --quiet brightness-ctl.service; then
    echo "Restarting brightness-ctl service..."
    systemctl --user restart brightness-ctl.service
else
    echo "Starting brightness-ctl service..."
    systemctl --user start brightness-ctl.service || {
        echo "WARN: could not start service (may require graphical session)" >&2
    }
fi

echo "Done. brightness-ctl is now available at $INSTALL_BIN"
