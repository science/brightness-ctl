# brightness-ctl

Color temperature and brightness daemon for X11/Cinnamon desktops.

## What it does

- **Color temperature**: automatic time-based transitions (dawn/dusk ramps) with manual warmer/cooler hotkey adjustment
- **Software brightness**: via `gammastep` one-shot mode (fast, GPU-based)
- **Hardware brightness**: via `ddcutil` DDC/CI (controls monitor backlight)
- **Ambient light**: USB camera reads room brightness and auto-adjusts (optional)
- **Hotkey response**: <50ms via Unix socket IPC to persistent daemon

## Requirements

- Python 3.12+
- `gammastep`, `ddcutil`, `libnotify-bin` (system packages)
- X11 with RandR (Cinnamon, MATE, etc.)

## Install

```bash
./install.sh
```

This symlinks `brightness-ctl` to `~/.local/bin/` and installs a systemd user service.

## Usage

```bash
brightness-ctl daemon          # Start daemon (or use systemd)
brightness-ctl warmer          # Shift color warmer by 200K
brightness-ctl cooler          # Shift color cooler by 200K
brightness-ctl toggle          # Toggle color shift on/off
brightness-ctl reset           # Reset color offset to zero
brightness-ctl bright-up       # Increase brightness (SW first, then HW)
brightness-ctl bright-down     # Decrease brightness (HW first, then SW)
brightness-ctl status          # Show current state
brightness-ctl stop            # Stop daemon
```

## Hotkeys (Cinnamon)

| Key | Action |
|-----|--------|
| Alt+KP_Add | Warmer |
| Alt+KP_Subtract | Cooler |
| Alt+End | Toggle |
| Alt+Page_Up | Brightness up |
| Alt+Page_Down | Brightness down |

## Development

```bash
pytest tests/ -v             # Run all tests
pytest tests/ -v -x          # Stop on first failure
```

See [CLAUDE.md](CLAUDE.md) for development methodology (TDD red/green) and architecture details.
See [PLAN.md](PLAN.md) for implementation plan and design decisions.
