# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Color temperature and brightness daemon for X11/Cinnamon desktops. Manages software brightness (via `gammastep` one-shot), hardware brightness (via `ddcutil` DDC/CI), time-based color temperature transitions, and camera-based ambient light sensing. Replaces a bash script with a Python asyncio daemon using Unix socket IPC for instant hotkey response.

For hardware-specific UAT and bring-up on a new machine, see `HOST_UAT.md`. `PLAN.md` is a historical planning artifact; prefer this file and the code for current architecture.

## Project Structure

```
src/
  brightness-ctl       # Entry point (#!/usr/bin/env python3) — daemon or CLI dispatcher
  daemon.py            # asyncio loop: socket server, periodic apply, ambient light loop
  cli.py               # Socket client: connect, send JSON command, print response
  color_temp.py        # get_base_temp(hour, minute, config) — pure math
  brightness.py        # bright_up/down state machine — pure logic
  autobrightness.py    # compute_target / compute_ambient_pct / calibration predicates — pure
  luminance_log.py     # append_reading / load_readings / compute_calibration / rotate_logs
  camera.py            # V4L2 ioctls (ctypes/fcntl/mmap), VID:PID resolver, capture filter
  hardware.py          # HardwareBackend protocol + SubprocessBackend (gammastep, ddcutil)
  config.py            # TOML config loading (stdlib tomllib), DEFAULT_CONFIG
  state.py             # JSON state with atomic writes (.tmp + os.rename)
  notify.py            # notify-send wrapper with persistent replace-id
tests/
  conftest.py               # Shared fixtures: MockHardwareBackend, temp dirs, factories
  test_color_temp.py        # Dawn/dusk transitions, edge cases, clamping
  test_brightness.py        # SW-before-HW up, HW-before-SW down, boundaries
  test_autobrightness.py    # compute_target math, calibration-ready predicate
  test_luminance_log.py     # JSONL round-trip, lookback windows, calibration percentiles
  test_state.py             # Round-trip, atomic write, missing file defaults
  test_config.py            # TOML loading, defaults, bash config migration
  test_daemon.py            # Socket IPC, command dispatch, debouncing, periodic apply
  test_camera.py            # Pure logic: VID:PID select, capture filter, YUYV extract
  test_hardware.py          # MockHardwareBackend verifies sequential DDC/CI calls
  test_notify.py            # replace-id persistence
```

## Running Tests

```bash
pytest tests/ -q              # Fast summary — the normal dev loop
pytest tests/ -v              # Verbose per-test output
pytest tests/ -v -x           # Stop on first failure
pytest tests/test_color_temp.py -v          # Run a single test file
pytest tests/ -k "test_dawn"                # Tests matching a name pattern
pytest tests/test_camera.py -v -k "capture" # File + name filter
```

Tests require only `python3-pytest` and optionally `python3-pytest-asyncio` for async daemon tests. No hardware, display, or sudo required for unit tests.

### Dev loop when the daemon is installed

`install.sh` symlinks `~/.local/bin/brightness-ctl` → `src/brightness-ctl`, so edits to any `src/*.py` are live after restarting the service:

```bash
pytest tests/ -q                                        # stay green first
systemctl --user restart brightness-ctl                 # pick up code changes
journalctl --user -u brightness-ctl -f --no-pager       # watch stderr/notify
```

Edit → pytest → restart → journal. The systemd user unit is in `~/.config/systemd/user/brightness-ctl.service`; it sets `ExecStart` to the symlinked CLI so there's no separate "install step" in the inner loop.

### Testing gap: the V4L2 ioctl layer is NOT tested

`test_camera.py` covers the *pure* logic in `camera.py` (VID:PID resolution, YUYV luminance extraction, selection filters) but does **not** exercise `open_camera()`, `capture_luminance()`, or `close_camera()` against a real V4L2 device — none of the tests issue ioctls. This gap has bitten us once already: the `v4l2_buffer` and `v4l2_format` ctypes structs had 64-bit ABI mismatches (wrong sizes, shifted fields) that all 200 tests happily ignored while real hardware silently returned all-zero frames.

**If you change anything under `# --- V4L2 ioctls via ctypes ---` in `camera.py`**, you must verify against real hardware. The short form is:

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from camera import resolve_camera_device, open_camera, capture_luminance, close_camera, probe_has_video_capture
dev = resolve_camera_device(None, capture_check=probe_has_video_capture)
h = open_camera(dev, brightness=32)
print('device:', dev, 'lum:', capture_luminance(h, 5))
close_camera(h)
"
```

Expected: a non-zero luminance reading in the 10–200 range for normal room lighting on an Alcor 058f:5608. All-zero, `EINVAL`, or a traceback means the ABI or flow is wrong — fix before committing.

## Development Methodology: TDD Red/Green

**All new functionality MUST follow Test-Driven Development:**

1. **RED**: Write a failing test first, run `pytest tests/ -v -x` to prove it fails
2. **GREEN**: Write minimal code to pass, run to prove it passes
3. **REFACTOR**: Clean up while keeping tests green
4. **REPEAT**: Build functionality incrementally with test coverage

### Key Principles

- Never skip the RED step — running before implementation proves the test can fail
- Small increments — each test covers one small behavior
- Pure functions first — `color_temp.py` and `brightness.py` are pure math with no I/O, making them trivially testable
- Dependency injection for hardware — all external tool calls go through `HardwareBackend` protocol; tests use `MockHardwareBackend`
- Test the state machine, not the subprocess — verify call sequences and state transitions, not that `ddcutil` actually works

### TDD Workflow Per Module

```
1. Create test file:     tests/test_<module>.py
2. Write first test:     def test_<behavior>():  # assert expected behavior
3. Run (RED):            pytest tests/test_<module>.py -v -x  # must fail
4. Create source file:   src/<module>.py
5. Write minimal code:   just enough to pass the test
6. Run (GREEN):          pytest tests/test_<module>.py -v -x  # must pass
7. Next test:            repeat from step 2
```

## Architecture

### Daemon (asyncio single-thread)

The daemon runs as a single asyncio event loop:
- **Unix socket server** at `$XDG_RUNTIME_DIR/brightness-ctl.sock` — handles CLI commands
- **Periodic apply** — time-based color temperature transitions
- **Debounce** — 100ms coalesce window after state changes before applying
- **Ambient light task** — reads camera every `autobrightness_interval` seconds (default 60s), proposes brightness adjustments

All state lives in memory; written to `~/.config/brightness-ctl/state.json` on mutation for crash recovery. Single-threaded asyncio means no locking needed.

### CLI (socket client)

Hotkey presses invoke `brightness-ctl warmer` etc. The entry point connects to the daemon socket, sends a JSON command, reads the response, and exits. Latency target: <50ms total.

Commands: `daemon`, `status`, `stop`, `warmer`, `cooler`, `toggle`, `reset`, `bright-up`, `bright-down`, plus the auto-brightness family: `auto-on`, `auto-off`, `auto-status`, `auto-calibrate`. All go through the same socket protocol and are dispatched in `daemon.py`.

### Hardware Backend

All external tool calls go through a `HardwareBackend` protocol:
- `SubprocessBackend` — real implementation using `asyncio.create_subprocess_exec`
- `MockHardwareBackend` — records call log for test assertions

**DDC/CI calls MUST be sequential** (not parallel) to avoid I2C bus contention across monitors.

### Camera (V4L2)

- Ambient sensor: Alcor Micro **USB 058f:5608** (`ALCOR_AMBIENT_VIDPID`)
- **BLOCKED**: Logitech HD Webcam C615 **USB 046d:082c** (meeting camera) — in `BLOCKED_VIDPIDS` in `camera.py`
- **Device node numbers (`/dev/video0`, `/dev/video1`, ...) are NOT stable** — they are assigned by `uvcvideo` in USB probe order and can flip across reboots/hotplugs. Do NOT encode "the Alcor is at /dev/videoN" as a rule anywhere. Always resolve by VID:PID via `resolve_camera_device()`.
- Config key `camera_device` defaults to `None` (auto-probe). If explicitly set, the resolver still refuses any node whose USB VID:PID is in `BLOCKED_VIDPIDS`.
- **Metadata-node filtering**: uvcvideo exposes the Alcor as TWO `/dev/videoN` nodes with the same VID:PID — one `V4L2_CAP_VIDEO_CAPTURE`, one `V4L2_CAP_META_CAPTURE`. Which gets the lower number is non-deterministic. `resolve_camera_device(capture_check=probe_has_video_capture)` runs `VIDIOC_QUERYCAP` on each candidate and skips the metadata node. Production callers (the daemon, `open_camera`) must pass the filter; tests pass `capture_check=None` to stay hardware-free.
- **Sensor-specific brightness tuning**: the Alcor 058f:5608 module exposes only `V4L2_CID_BRIGHTNESS` — no gain, no exposure — and at the factory default of 0 produces near-black frames. `open_camera(brightness=N)` applies `V4L2_CID_BRIGHTNESS` after format negotiation. The host config sets `camera_brightness = 32` to land the mid-tone around Y≈63. If you move to a sensor with real exposure controls, this key becomes optional.
- **64-bit ctypes ABI assertions**: `v4l2_buffer` must be 88 bytes; `v4l2_format` must be 208 bytes. Both are asserted at module load because the field layouts are hand-rolled from `<linux/videodev2.h>` and a single missing padding field silently corrupts every ioctl. If you touch either struct, re-run the real-hardware smoke test above — the tests will pass even when the layout is wrong.
- Format: YUYV 160x120, raw ioctls via ctypes/fcntl/mmap
- Discard first frame after stream-on (warmup zeros)
- Average 3-5 frames to reduce noise

### Ambient loop resilience

`Daemon._ambient_light_loop` must survive real-world failures:

- `capture_luminance()` raises `OSError` on `select()` timeout (5s from a 30fps camera = stale fd). It does not silently return 0 — that would pollute the luminance log with fake dark readings and break calibration.
- On any `OSError` from capture, the loop closes the stale handle, re-resolves the device (in case uvcvideo assigned a new `/dev/videoN`), and reopens. If the reopen fails, it sleeps `autobrightness_interval` and retries forever — we assume a closed laptop lid or a detached USB camera will eventually come back, and we'd rather keep trying than mark the feature disabled.
- `resolve_camera_device()` and `open_camera()` failures at startup are **surfaced**, not swallowed: stderr line via `print(..., file=sys.stderr, flush=True)`, `notify-send` to the user, `state.autobrightness_enabled = False` so `auto-status` stops lying. Never revert to a silent `return` — that antipattern is what made earlier camera bugs invisible for weeks.

## Do Not Do (Safety Rules)

1. **Never open USB 046d:082c** (Logitech HD Webcam C615 — user's meeting camera). This is enforced in code by `BLOCKED_VIDPIDS` in `src/camera.py`. Never remove the C615 from that set. Never refer to "safe" or "unsafe" V4L2 devices by their `/dev/videoN` number — always by USB VID:PID, because device-node numbers are assigned at USB probe time and are not stable.
2. **Never run parallel ddcutil commands** — sequential only to prevent I2C bus contention.
3. **Never use pip/venv for runtime** — all runtime dependencies are Python 3.12 stdlib (asyncio, tomllib, ctypes, fcntl, mmap, json, struct, socket, subprocess).
4. **Never block the asyncio event loop** — use `create_subprocess_exec` for external tools, never `subprocess.run`.
5. **Never hardcode display count** — always detect via `ddcutil detect` (cached at startup, refreshed on SIGHUP).
6. **Never apply color temp and brightness separately** — always in a single `gammastep` call to prevent flickering.

## Dependencies

### Runtime (all stdlib, no pip)
- Python 3.12+ (for `tomllib`)
- `gammastep` (system package — color temperature)
- `ddcutil` (system package — DDC/CI monitor control)
- `notify-send` (from libnotify — notifications)

### Test
- `python3-pytest` (system package)
- `python3-pytest-asyncio` (for async daemon tests)

### Config & State
- Config: `~/.config/brightness-ctl/config.toml` (TOML, read via stdlib `tomllib`). Defaults live in `DEFAULT_CONFIG` in `src/config.py` — user file only needs to override.
- State: `~/.config/brightness-ctl/state.json` (JSON, atomic write via `os.rename`). Fields: `enabled`, `offset`, `sw_brightness`, `hw_brightness`, `autobrightness_enabled`, `anchor_combined`, `cal_min`, `cal_max`.
- Luminance log: `~/.config/brightness-ctl/luminance-logs/luminance-YYYY-MM-DD.log` — append-only JSONL, one reading per `luminance_log_interval` seconds (default 1800). Calibration consumes a 7-day rolling window.
- Socket: `$XDG_RUNTIME_DIR/brightness-ctl.sock`

## Install / Uninstall

```bash
./install.sh        # Symlinks to ~/.local/bin, installs systemd user service
./uninstall.sh      # Removes symlinks and service
```

## Git Workflow

- Single `main` branch for development
- No CI — tests run locally via `pytest tests/ -v`
- Commit test + implementation together after GREEN step
