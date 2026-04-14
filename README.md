# brightness-ctl

Color temperature and brightness daemon for X11/Cinnamon desktops. Python asyncio daemon with Unix-socket IPC; sub-50ms hotkey response. Replaces an earlier bash script.

## What it does

- **Color temperature**: automatic time-based dawn/dusk transitions with manual warmer/cooler hotkey adjustment.
- **Software brightness**: `gammastep` one-shot (GPU-based, fast).
- **Hardware brightness**: `ddcutil` DDC/CI (monitor backlight).
- **Ambient auto-brightness** (optional): reads a USB camera every 60s, calibrates against a 7-day rolling window of luminance samples (≥30 readings required), and drives monitor brightness to track the room. Manual hotkey adjustments are honored — the daemon back-computes the anchor so the ambient loop doesn't fight the user. Survives suspend/resume and USB hotplugs — the daemon reopens the camera when the fd goes stale.
- **Camera-safety interlock**: devices are selected by USB VID:PID, never by `/dev/videoN` number (those flip across reboots). A hard blocklist refuses to open known meeting cameras even if explicitly configured.

## Requirements

- Python 3.12+ (stdlib only — no pip/venv at runtime)
- `gammastep`, `ddcutil`, `libnotify-bin` (system packages)
- X11 with RandR (Cinnamon, MATE, XFCE, …)
- An ambient-light sensor for auto-brightness (optional). Currently tuned for the Alcor Micro 058f:5608 module; other UVC cameras work but may need `camera_brightness` tweaked per sensor.

## Install

```bash
./install.sh
```

Symlinks `brightness-ctl` to `~/.local/bin/` and installs a systemd user service (`~/.config/systemd/user/brightness-ctl.service`). The service is enabled by default and starts on login. `./uninstall.sh` reverses both.

## CLI

```bash
brightness-ctl daemon          # Start daemon (systemd does this automatically)
brightness-ctl status          # Show current color temp + brightness state
brightness-ctl stop            # Stop daemon

# Color temperature
brightness-ctl warmer          # Shift color warmer by one step
brightness-ctl cooler          # Shift color cooler by one step
brightness-ctl toggle          # Enable/disable the color shift entirely
brightness-ctl reset           # Reset color offset to zero

# Brightness
brightness-ctl bright-up       # Increase brightness (SW first, then HW)
brightness-ctl bright-down     # Decrease brightness (HW first, then SW)

# Ambient auto-brightness
brightness-ctl auto-on         # Start the camera loop, anchor at current brightness
brightness-ctl auto-off        # Stop the loop, release the camera
brightness-ctl auto-status     # Report state + calibration + anchor
brightness-ctl auto-calibrate  # Recompute cal_min/cal_max from recent luminance log
brightness-ctl auto-set-cal 20 180  # Manually set calibration range
brightness-ctl auto-reset-cal      # Clear calibration + delete logs
```

## Hotkeys (Cinnamon)

| Key              | Action            |
|------------------|-------------------|
| Alt+KP_Add       | Warmer            |
| Alt+KP_Subtract  | Cooler            |
| Alt+End          | Toggle color shift|
| Alt+Page_Up      | Brightness up     |
| Alt+Page_Down    | Brightness down   |

## Configuration

`~/.config/brightness-ctl/config.toml`. Defaults live in `src/config.py`; only override the keys you care about. Selected keys:

```toml
# Color temperature
day_temp = 2800
night_temp = 2200
dawn_start = 6
dawn_end = 8
dusk_start = 18
dusk_end = 20
step = 200

# Brightness
hw_step = 5
sw_step = 5
sw_min = 10

# Auto-brightness (optional)
camera_device = null            # null = auto-probe by VID:PID
camera_brightness = 32          # V4L2_CID_BRIGHTNESS; Alcor needs this
camera_frames = 4               # frames to average per capture
autobrightness_interval = 60    # seconds between camera reads
autobrightness_range = 40       # max deviation from anchor, 0..200 scale
luminance_log_interval = 1800   # seconds between log writes (default 30min)
calibration_lookback_days = 7
```

## Development

```bash
pytest tests/ -q              # Fast test run — expect everything green
pytest tests/ -v -x           # Verbose, stop on first failure
pytest tests/test_camera.py   # Run a single file
```

Edit → pytest → `systemctl --user restart brightness-ctl` → tail the journal. `install.sh` symlinks the source, so code changes are live after a restart.

See [CLAUDE.md](CLAUDE.md) for architecture, the V4L2/ctypes hardware layer, and the gaps in test coverage that require real-hardware verification. See [HOST_UAT.md](HOST_UAT.md) for the end-to-end acceptance checklist and the hardware bring-up procedure for a new machine. [PLAN.md](PLAN.md) is the original planning document, kept as history.
