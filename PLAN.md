# Plan: Extract redshift-ctl bash script into ~/dev project and rewrite as Python daemon (brightness-ctl)

## Context

The current `redshift-ctl` is a 384-line bash script managed by yadm that has grown beyond dotfile scope. It manages color temperature (via `gammastep -P -O` one-shot calls) and brightness (SW via gammastep, HW via `ddcutil` DDC/CI) across 3 monitors. It has three bugs: hotkey response takes 500-2000ms because each keypress spawns a shell and blocks on gammastep; parallel `ddcutil` I2C commands cause monitors to update inconsistently; and brightness changes can inadvertently reset color temperature because `gammastep -P` wipes prior state and there's no coordination between the daemon's 30s apply loop and hotkey-triggered applies. The user also wants to add camera-based ambient light detection. This complexity warrants a proper project with a persistent daemon, socket IPC, and TDD.

## VM Testing: Not Needed

Unlike pam-fprintd-sudo (which can lock you out), worst case here is neutral 6500K and default brightness. Recovery is trivial: `gammastep -P -x` and `ddcutil setvcp 10 50`. All core logic (temp calculation, brightness state machine, debouncing) is pure functions testable without hardware. DDC/CI and camera need real hardware, but the blast radius is cosmetic. Build and test on bare metal.

## Project Structure

```
~/dev/brightness-ctl/
  CLAUDE.md                 # Project rules and architecture
  README.md
  PLAN.md
  install.sh                # Symlinks, systemd service, config migration
  uninstall.sh
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

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language | Python 3.12 | stdlib asyncio, tomllib, struct/fcntl for V4L2 — no pip deps at runtime |
| Daemon arch | asyncio single-thread | Socket server + periodic timers + sequential DDC/CI without threads |
| IPC | Unix domain socket (`$XDG_RUNTIME_DIR/brightness-ctl.sock`) | Bidirectional, asyncio-native, sub-ms latency |
| Config format | TOML | stdlib `tomllib` in 3.12; install.sh auto-migrates old bash config |
| Camera capture | Raw V4L2 ioctls (ctypes/fcntl/mmap) | Confirmed working on Alcor 058f:5608 at /dev/video2; avoids 500MB OpenCV |
| DDC/CI | Sequential `await` per monitor | Fixes I2C bus contention; cache `ddcutil detect` at daemon startup |
| Packaging | No pip/venv | Plain scripts + symlinks, matching audio-switcher pattern |

## Camera Hardware (Confirmed)

- **Device**: Alcor Micro Corp. USB Camera (058f:5608) — cheap $7 USB webcam for ambient light only
- **Video node**: `/dev/video2` (capture), `/dev/video3` (metadata — ignore)
- **Do NOT touch**: `/dev/video0` `/dev/video1` = Logitech HD Webcam C615 (046d:082c) — user's meeting camera
- **Driver**: uvcvideo
- **Format**: YUYV 4:2:2 only, resolutions: 640x480, 320x240, 160x120, 352x288, 176x144
- **Capture method**: V4L2 raw ioctls via Python `ctypes` + `fcntl` + `mmap` (confirmed working)
- **Use 160x120** (smallest) — 38400 bytes/frame, 19200 Y-channel samples
- **Warmup**: frame 0 is all zeros; need to discard first frame after stream-on
- **Noise**: significant per-frame variance (~4-7 avg_Y in stable dim lighting); use rolling average of 3-5 frames
- **V4L2 struct note**: `v4l2_format` needs explicit 4-byte padding between `type` and `fmt` union on amd64 (total 208 bytes)
- **Config must specify device path** (default `/dev/video2`) so it never touches the Logitech

## Bug Fixes

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| **Slow hotkeys** (500-2000ms) | Shell spawn + `bc -l` + blocking `gammastep -P -O` per keypress | CLI sends JSON over socket (~1ms). Daemon holds state in memory, applies async. |
| **Inconsistent monitors** | Parallel `ddcutil setvcp &` causes I2C bus contention | Sequential `await` loop. Cache display list at startup (re-detect on SIGHUP). |
| **Color temp reset on brightness change** | `gammastep -P` resets all state; daemon apply() races hotkey apply() | Single daemon owns all applies. 100ms debounce coalesces rapid commands. One apply at a time (asyncio single-thread). |

## Implementation Phases

### Phase 1: Project Scaffold + Core Logic (TDD)

Pure functions, no hardware, no daemon. Every module gets tests first.

1. **Scaffold**: create `~/dev/brightness-ctl/`, init git, write CLAUDE.md with project rules
2. **`config.py`** + `test_config.py`: TOML loading with defaults, validation, bash config migration
3. **`color_temp.py`** + `test_color_temp.py`: port `get_base_temp()` — pure function of (hour, minute, config). Tests: midnight=NIGHT_TEMP, noon=DAY_TEMP, dawn/dusk linear interpolation, boundary minutes, clamping with offset
4. **`brightness.py`** + `test_brightness.py`: state machine returning `(new_state, action)` tuples. Tests: SW up before HW, HW down before SW, clamp at min/max, boundary transitions between SW and HW ranges
5. **`state.py`** + `test_state.py`: JSON read/write with atomic rename. Tests: round-trip, missing file defaults, concurrent-safe rename
6. **`notify.py`**: thin wrapper, minimal tests (command construction only)

**Exit criteria**: ~50 unit tests passing, `pytest tests/` green with no hardware

### Phase 2: Hardware Backend + Daemon

7. **`hardware.py`** + `test_hardware.py`: define `HardwareBackend` protocol:
   - `apply_color_temp(temp, brightness, method)` — wraps `gammastep -m METHOD -P -O TEMP -b BRI:BRI`
   - `reset_color_temp(method)` — wraps `gammastep -m METHOD -P -x`
   - `detect_displays() -> list[DisplayInfo]` — wraps `ddcutil detect`, cached
   - `set_hw_brightness(display_id, value)` — wraps `ddcutil -d ID setvcp 10 VALUE`
   - `SubprocessBackend` uses `asyncio.create_subprocess_exec`
   - `MockHardwareBackend` records call log for test assertions
   - Tests verify: sequential DDC/CI call order, caching of detect, error handling

8. **`cli.py`**: socket client — connect to daemon, send `{"cmd": "warmer"}`, read `{"status": "ok", ...}`, print message. Falls back to error if daemon not running.

9. **`daemon.py`** + `test_daemon.py`: asyncio event loop with:
   - `asyncio.start_unix_server` for command handling
   - Periodic `apply` every 30s for time-based transitions
   - **Debounce**: on state mutation, schedule apply in 100ms; reset timer on new command
   - Command dispatch: warmer, cooler, toggle, reset, bright-up, bright-down, status, stop
   - All state in memory; writes to `state.json` on mutation for crash recovery
   - Tests: mock backend, in-process daemon, verify command sequences and debounce timing

10. **`brightness-ctl` entry point**: detects subcommand — `daemon` starts the loop, everything else runs `cli.py`

**Exit criteria**: ~80 tests passing. Can start daemon, send commands via socket, see mock backend calls in correct order with debouncing.

### Phase 3: Camera Ambient Light

11. **`camera.py`** + `test_camera.py`:
    - `CameraCapture` class: opens `/dev/video2` (configurable), sets YUYV 160x120, manages V4L2 mmap buffer lifecycle
    - `capture_luminance() -> float` — streams on, discards first frame (warmup zeros), averages Y channel of 3 frames for noise reduction, returns 0.0-1.0
    - `luminance_to_brightness(luminance, curve) -> int` — piecewise linear mapping (pure math, easily tested)
    - Auto-adjust logic: proposes brightness every 60s. After manual hotkey override, holds for configurable period (default 10min) before resuming auto-adjust.
    - V4L2 structs via `ctypes` (confirmed working: `v4l2_format` needs 4-byte pad on amd64)
    - Tests: luminance mapping math, hold timer logic (with mocked clock), frame averaging, edge cases. Camera capture itself tested manually.
    - **Important**: config must default to `/dev/video2` and never touch `/dev/video0` (Logitech C615 meeting camera)

12. **Integrate into daemon**: add `ambient_light_task` to asyncio loop. Manual commands set hold timestamp. Config has `[camera]` section: `device`, `interval`, `hold_minutes`, `curve` (list of `[luminance, brightness]` pairs).

**Exit criteria**: ~90+ tests. Camera-driven brightness adjustments work with mock capture values.

### Phase 4: Install + Migration

13. **`install.sh`** (following `~/dev/audio-switcher/install.sh` pattern):
    - Verify Python 3.12+
    - Stop old daemon if running (check `/tmp/brightness-ctl-daemon.pid`)
    - Migrate bash config to TOML if old format exists
    - Migrate state file from key=value to JSON
    - Symlink `src/brightness-ctl` -> `~/.local/bin/brightness-ctl`
    - Install systemd user service:
      ```ini
      [Unit]
      Description=Color temperature and brightness daemon
      After=graphical-session.target

      [Service]
      Type=simple
      ExecStart=%h/.local/bin/brightness-ctl daemon
      Restart=on-failure
      RestartSec=5
      Environment=DISPLAY=:0

      [Install]
      WantedBy=default.target
      ```
    - `systemctl --user daemon-reload && enable && start`

14. **`uninstall.sh`**: stop + disable service, remove service file, remove symlinks, daemon-reload

15. **Manual smoke test**: run `install.sh`, press all hotkeys (Alt+PgUp/PgDn, Alt+KP+/-, Alt+End), verify notifications appear fast and all 3 monitors respond consistently. Run `brightness-ctl status`.

### Phase 5: yadm Cleanup

16. **Remove old files from yadm**:
    - `yadm rm ~/.local/bin/brightness-ctl` (old bash script)
    - `yadm rm ~/.local/bin/brightness` (old bash script)
    - `yadm rm ~/.local/bin/gammastep-autostart` (old wrapper)
    - Remove or update `~/.config/autostart/gammastep-indicator.desktop` (systemd `WantedBy=default.target` handles autostart now)

17. **Add to yadm** (if not already tracked):
    - `~/.config/brightness-ctl/config.toml` — user settings

18. **Update yadm tests** (`~/.config/yadm/test-dotfiles.sh`):
    - "brightness-ctl is installed" → check symlink points to `~/dev/brightness-ctl/src/brightness-ctl` (same pattern as audio-switcher test)
    - "brightness-ctl systemd service is enabled" → `systemctl --user is-enabled brightness-ctl`
    - "brightness-ctl systemd service is active" → `systemctl --user is-active brightness-ctl`
    - Config tests → check TOML format (`config.toml` with `day_temp`)
    - Remove gammastep-autostart tests (replaced by systemd)
    - Keep: gammastep installed, ddcutil installed, keybinding tests (commands unchanged)

19. **Keybindings unchanged**: dconf entries still call `brightness-ctl warmer` etc. — same CLI name, same subcommands, just faster.

20. **Package dependencies** — add to `~/.config/yadm/packages/apt-linux-bambam.txt`:
    - `python3-pytest` (test runner)
    - `v4l-utils` (camera diagnostics via `v4l2-ctl`)

## Verification

After full implementation:

1. **Unit tests**: `cd ~/dev/brightness-ctl && pytest tests/ -v` — all green, no hardware needed
2. **Daemon start**: `systemctl --user start brightness-ctl && brightness-ctl status`
3. **Hotkey latency**: press Alt+PgUp — notification should appear in <100ms (vs 500-2000ms before)
4. **Monitor consistency**: press Alt+PgUp 5 times, all 3 monitors should be at same brightness
5. **Color stability**: press Alt+PgUp/PgDn repeatedly — color temperature should never reset to 6500K
6. **Camera**: `brightness-ctl status` should show ambient light reading and auto-adjusted brightness
7. **yadm tests**: `bash ~/.config/yadm/test-dotfiles.sh` — all passing
8. **Reboot survival**: reboot, verify daemon auto-starts and state is restored
