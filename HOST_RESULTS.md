# Host-OS results from bambam — 2026-04-10

Results of running `HOST_QUESTIONS.md` on the real host. **Read the
"CRITICAL" section first before doing anything else.**

---

## 🚨 CRITICAL: camera device nodes are INVERTED on the host right now

Per `udevadm info` on bambam at the time of this run:

| Device       | Vendor:Model  | Product                                     |
|--------------|---------------|---------------------------------------------|
| `/dev/video0` | **058f:5608** | Alcor "USB Camera" (ambient sensor — SAFE) |
| `/dev/video1` | 058f:5608     | Alcor metadata node                         |
| `/dev/video2` | **046d:082c** | **Logitech HD Webcam C615 — MEETING CAM**   |
| `/dev/video3` | 046d:082c     | Logitech metadata node                      |

`CLAUDE.md`, `src/camera.py` default, and every safety rule in the repo
assume `/dev/video2` = Alcor. On the real host **it is the Logitech**.

**Auto-brightness is currently disabled** (`state.json` has
`autobrightness_enabled=false`), so nothing has actually been opened yet.
But enabling or calibrating as-is would open the meeting cam.

### Why this happened (root cause)

Device-node ctimes on the host tell the story:

```
crw-rw----+ 1 root video 81, 0 Mar 26 11:05 /dev/video0   # Alcor
crw-rw----+ 1 root video 81, 1 Mar 26 11:05 /dev/video1   # Alcor
crw-rw----+ 1 root video 81, 2 Mar 27 10:56 /dev/video2   # Logitech
crw-rw----+ 1 root video 81, 3 Mar 27 10:56 /dev/video3   # Logitech
```

`/dev/videoN` numbers are assigned by `uvcvideo` in **USB enumeration
order at probe time**. They are not stable across reboots, hotplugs, or
USB port changes. Whichever camera the kernel probes first wins the
lower number.

When Phase 3 was written, the Alcor must have been plugged in *after*
the Logitech on that boot, so it landed at `video2`. The "Logitech = 0/1,
Alcor = 2" rule got baked into `CLAUDE.md` and the code default as if it
were a hardware fact. It isn't — it's a race.

On this host right now (post-reboot on Mar 26), the Alcor was already
attached at boot and the Logitech was plugged in the next day (Mar 27).
Probe order flipped, so the numbering flipped. The "safety rule" in
`CLAUDE.md` is now pointing the gun at the wrong foot.

### Fix options (must choose one before re-enabling auto-brightness)

1. **Pin to `/dev/v4l/by-id/...`** — these symlinks are stable per USB
   vendor/product/serial. Hard-code the Alcor's `by-id` path as the
   default, or resolve it at runtime.
2. **Probe by USB VID:PID** — walk `/sys/class/video4linux/video*` and
   pick the node whose parent device matches `058f:5608`. Refuse to run
   if `046d:082c` is the only match. This is the most defensive option.
3. **Require explicit `[camera] device=` in config** — no default at
   all. Safer, but user has to hand-pick every time.
4. **udev rule** — ship a udev rule that creates `/dev/alcor-ambient`
   pointing at the right node. Cleanest but adds a system-level install
   step.

Recommendation: **option 2** (probe by VID:PID with C615 blocklist).
It's code-only, no install-step regressions, and it actively refuses to
open the forbidden device even if someone sets it in config by mistake.

Also: `CLAUDE.md` needs to stop phrasing the safety rule in terms of
device-node numbers entirely. Rule should be "never open USB
`046d:082c`", not "never open `/dev/video0`".

---

## Section-by-section results

### 1. Environment
- `linux-bambam`, user `steve`, kernel `6.8.0-106-generic`, Python `3.12.13` ✓

### 2. Virtiofs / git
- Both target commits present on host: `8905dec` (Phase 4), `0d84770` (Phase 3) ✓
- Branch is 2 ahead of `origin/main` (unpushed)
- `HOST_QUESTIONS.md` untracked on host (expected — written from VM, visible via virtiofs)

### 3. Installed / running
- `~/.local/bin/brightness-ctl` → symlink to `~/dev/brightness-ctl/src/brightness-ctl` ✓
- `~/.local/bin/redshift-ctl` gone ✓
- `~/.local/bin/gammastep-autostart` still present (bash-era script, 854 bytes)
- **Daemon is running** as pid `1619201`: bare `python3 .../brightness-ctl daemon`, **not** under systemd
- No `gammastep` processes (expected — it's invoked one-shot)

### 4. systemd
- `brightness-ctl.service` **not installed** anywhere
- `systemctl --user is-enabled brightness-ctl` → `not-found`
- Phase 4 `install.sh` has **not** been run on this host yet

### 5. Config & state
- `~/.config/brightness-ctl/config.toml` (188 bytes):
  ```
  day_temp = 2800
  night_temp = 2200
  step = 200
  min_temp = 1500
  max_temp = 6500
  dawn_start = 6
  dawn_end = 8
  dusk_start = 18
  dusk_end = 20
  method = "randr"
  hw_step = 5
  sw_step = 5
  sw_min = 10
  ```
  **No `[camera]` section** → code default (`/dev/video2`) would be used
  → would open the Logitech. See critical section above.
- `~/.config/brightness-ctl/state.json`:
  ```json
  {
    "enabled": true,
    "offset": -400,
    "sw_brightness": 100,
    "hw_brightness": 0,
    "autobrightness_enabled": false,
    "anchor_combined": null,
    "cal_min": null,
    "cal_max": null
  }
  ```
- No `luminance-logs/` dir yet (not created until auto-brightness runs)
- **Stale bash-era leftovers still present** in `~/.config/brightness-ctl/`:
  - `config` (Feb 19, 834 bytes) — old bash script config
  - `state` (Mar 16, 56 bytes) — old bash script state
  Safe to delete.

### 6. Other bash-era leftovers
- `~/.config/redshift-ctl/` gone ✓
- **`~/.config/autostart/gammastep-indicator.desktop` still present** (Feb 24) — bash-era autostart, should be removed

### 7. Hardware prerequisites
- `gammastep`, `ddcutil`, `notify-send` all in `/usr/bin` ✓
- `ddcutil 1.4.1` with USB support
- **3 displays detected** via `ddcutil detect`:
  - Display 1 → `/dev/i2c-3`
  - Display 2 → `/dev/i2c-4`
  - Display 3 → `/dev/i2c-5`

### 8. Camera
- `v4l2-ctl` is **not installed** on the host — fell back to `udevadm info`
- See critical section at the top for the VID:PID mapping

### 9. Unit test regression
- `pytest tests/ -q`: **181 passed in 0.40s** ✓
- Cosmetic warning: `Task was destroyed but it is pending!` for
  `Daemon._ambient_light_loop` at teardown in 3 tests. Not a failure —
  the tests aren't cancelling the ambient-light task cleanly before the
  event loop closes. Worth fixing as a polish item, but not blocking.

### 10. Socket / runtime
- `XDG_RUNTIME_DIR=/run/user/1000` ✓
- `/run/user/1000/brightness-ctl.sock` exists, mtime `Apr 6 10:03` →
  **belongs to the currently running stale daemon** (pre-Phase-3 code)

### 11. Cinnamon hotkeys
- Custom keybindings all wired to `/home/steve/.local/bin/brightness-ctl`:
  `warmer`, `cooler`, `toggle`, `bright-up`, `bright-down` ✓

---

## Recommended order of operations (none of this done yet)

1. **Fix the camera-selection logic** (option 2 above: probe by VID:PID,
   blocklist `046d:082c`). Update `CLAUDE.md` safety rule to speak in
   VID:PID, not node numbers. Add a test that asserts the blocklist
   rejects the C615.
2. **Kill the stale daemon** (`pid 1619201`) and its socket once the
   camera fix is in, so the new code is actually running.
3. **Run `./install.sh` fresh** on the host to get the systemd user unit
   in place (Phase 4). Verify `systemctl --user status brightness-ctl`
   comes up clean.
4. **Clean up stale leftovers**:
   - `rm ~/.config/brightness-ctl/{config,state}` (bash-era)
   - `rm ~/.config/autostart/gammastep-indicator.desktop`
   - Decide fate of `~/.local/bin/gammastep-autostart` (bash-era helper)
5. **Only then**: run the smoke-test checklist (hotkey latency, monitor
   consistency, color stability, auto-brightness enable/calibrate/adjust).
6. Fix the `_ambient_light_loop` teardown warning in tests (polish).
