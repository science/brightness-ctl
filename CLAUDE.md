# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

Color temperature and brightness daemon for X11/Cinnamon desktops. Manages software brightness (via `gammastep` one-shot), hardware brightness (via `ddcutil` DDC/CI), time-based color temperature transitions, and camera-based ambient light sensing. Replaces a bash script with a Python asyncio daemon using Unix socket IPC for instant hotkey response.

## Project Structure

```
src/
  brightness-ctl            # Entry point (#!/usr/bin/env python3) — daemon or CLI
  daemon.py               # asyncio loop: socket server, periodic apply, ambient light
  color_temp.py           # get_base_temp(hour, minute, config) -> int (pure math)
  brightness.py           # bright_up/down state machine (pure logic)
  hardware.py             # HardwareBackend protocol + SubprocessBackend (gammastep, ddcutil)
  camera.py               # V4L2 ambient light capture + lux-to-brightness mapping
  config.py               # TOML config loading (stdlib tomllib)
  state.py                # JSON state with atomic writes (write .tmp + os.rename)
  notify.py               # notify-send wrapper with replace-id
  cli.py                  # Socket client: connect, send JSON command, print response
tests/
  conftest.py             # Shared fixtures: MockHardwareBackend, temp dirs, config factories
  test_color_temp.py      # Dawn/dusk transitions, edge cases, clamping
  test_brightness.py      # SW-before-HW up, HW-before-SW down, boundaries
  test_state.py           # Round-trip, atomic write, missing file defaults
  test_config.py          # TOML loading, defaults, bash config migration
  test_daemon.py          # Socket IPC, command dispatch, debouncing, periodic apply
  test_camera.py          # Luminance-to-brightness mapping, hold-after-override
  test_hardware.py        # MockHardwareBackend verifies sequential DDC/CI calls
  test_integration.py     # Real daemon subprocess, socket commands, state verification
```

## Running Tests

**Always use pytest to run tests:**

```bash
pytest tests/ -v              # Run all tests
pytest tests/ -v -x           # Stop on first failure
pytest tests/test_color_temp.py -v   # Run single test file
pytest tests/ -k "test_dawn"  # Run tests matching pattern
```

Tests require only `python3-pytest` and optionally `python3-pytest-asyncio` for async daemon tests. No hardware, display, or sudo required for unit tests.

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
- **Periodic apply** every 30s — time-based color temperature transitions
- **Debounce** — 100ms coalesce window after state changes before applying
- **Ambient light task** — reads camera every 60s, proposes brightness adjustments

All state lives in memory; written to `~/.config/brightness-ctl/state.json` on mutation for crash recovery. Single-threaded asyncio means no locking needed.

### CLI (socket client)

Hotkey presses invoke `brightness-ctl warmer` etc. The entry point connects to the daemon socket, sends a JSON command, reads the response, and exits. Latency target: <50ms total.

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
- Format: YUYV 160x120, raw ioctls via ctypes/fcntl/mmap
- Discard first frame after stream-on (warmup zeros)
- Average 3-5 frames to reduce noise

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
- Config: `~/.config/brightness-ctl/config.toml` (TOML, read via stdlib `tomllib`)
- State: `~/.config/brightness-ctl/state.json` (JSON, atomic write via `os.rename`)
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
