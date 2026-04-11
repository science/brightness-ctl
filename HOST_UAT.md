# brightness-ctl — Host UAT Guide

User acceptance testing for the Phase 3 (auto-brightness) + Phase 4
(systemd install) work. This file is the **single source of truth** for
what to run on `linux-bambam` to validate the daemon end-to-end. It is
written to be usable by both:

- A human (`steve`) working at the physical machine, and
- A Claude Code session running on the host, picking up from here.

The dev-VM Claude session wrote this file after committing the fixes
that address the findings originally captured in an earlier
`HOST_RESULTS.md` audit (now replaced by this doc). It is safe to work
from just this file — you do not need the old audit.

---

## 0. Preconditions & current state

**Branch:** `main`, 3 commits ahead of `origin/main`, not yet pushed.

**Relevant commits (should be visible on the host via virtiofs):**

| SHA | Summary |
|-----|---------|
| `0d84770` | Phase 3: camera-based auto-brightness |
| `8905dec` | Phase 4: install.sh writes systemd unit; uninstall.sh |
| `e3f8648` | camera: resolve ambient sensor by USB VID:PID, blocklist C615 |

Verify before starting:

```bash
cd ~/dev/brightness-ctl
git log --oneline -5   # e3f8648 should be at top
git status             # should be clean (or only this file modified)
pytest tests/ -q       # expect: 200 passed
```

If `pytest` doesn't report 200 passed, **stop** and investigate before
touching the running daemon.

---

## 1. Background: why the camera safety fix matters

The earlier audit revealed that `/dev/videoN` numbers on bambam are
**not** what the code assumed:

| Device        | VID:PID     | Product                                |
|---------------|-------------|----------------------------------------|
| `/dev/video0` | `058f:5608` | Alcor "USB Camera" (ambient sensor — **SAFE**) |
| `/dev/video1` | `058f:5608` | Alcor metadata node |
| `/dev/video2` | `046d:082c` | **Logitech HD Webcam C615 — MEETING CAM** |
| `/dev/video3` | `046d:082c` | Logitech metadata node |

The Phase 3 code defaulted `camera_device = "/dev/video2"`. Enabling
auto-brightness on the host as originally shipped would have opened the
meeting camera.

**Fix, as of `e3f8648`:**

- Device selection is now by USB VID:PID, via
  `resolve_camera_device()` in `src/camera.py`, which walks
  `/sys/class/video4linux/video*/device` and reads `idVendor` /
  `idProduct` from the USB parent.
- `ALCOR_AMBIENT_VIDPID = ("058f", "5608")` is the allowlisted device.
- `BLOCKED_VIDPIDS = {("046d", "082c")}` is a hard refuse list
  containing the C615. Even if `config.toml` sets `camera_device`
  explicitly to a C615 node, `open_camera` refuses.
- Config default `camera_device` is now `None` (auto-probe).
- **Node numbers are never trusted as identity anywhere in code or docs.**

UAT section 3 Phase C exists specifically to verify this fix against
real hardware **before** any command that opens the camera is run.

---

## 2. Pre-UAT host cleanup (do this in order)

### 2.1 Verify stale daemon state

```bash
pgrep -af brightness-ctl
# The daemon running at audit time was pid 1619201, a bare
# `python3 brightness-ctl daemon` NOT under systemd. If you still
# see a non-systemd daemon, it is still running Phase 2 code in memory
# and needs to die before the Phase 3 code can load.

ls -la /run/user/$(id -u)/brightness-ctl.sock
# Stale socket from the pre-Phase-3 daemon. Will be recreated by the new
# daemon, but kill it explicitly so the old one doesn't race us.
```

### 2.2 Kill the stale daemon and its socket

```bash
pkill -f "brightness-ctl daemon" || true
sleep 0.5
pgrep -af brightness-ctl   # expect: empty
rm -f /run/user/$(id -u)/brightness-ctl.sock
```

If `pkill` reports an error or the process sticks around, `kill -9` it
explicitly — **do not proceed while the Phase 2 daemon is still live**.

### 2.3 Clean bash-era leftovers

Safe removals identified by the audit:

```bash
# bash-era config + state files (superseded by config.toml / state.json)
rm -f ~/.config/brightness-ctl/config
rm -f ~/.config/brightness-ctl/state

# bash-era gammastep autostart (replaced by systemd unit in 8905dec)
rm -f ~/.config/autostart/gammastep-indicator.desktop
```

Leave alone for now:

- `~/.local/bin/gammastep-autostart` — small bash helper. Decide fate
  after smoke test passes (see section 6).
- `~/.config/brightness-ctl/config.toml` — still current, keep.
- `~/.config/brightness-ctl/state.json` — still current. The
  `autobrightness_enabled=false` / `null` anchor fields are expected
  defaults for a first run.

### 2.4 Fresh install (writes systemd unit)

```bash
cd ~/dev/brightness-ctl
./install.sh
```

Expected output includes:

- `Symlinked: ~/.local/bin/brightness-ctl -> ...`
- `Wrote systemd unit: ~/.config/systemd/user/brightness-ctl.service`
- `Starting brightness-ctl service...` (or `Restarting...` if it was
  already live)

Verify immediately:

```bash
systemctl --user status brightness-ctl        # active (running)
systemctl --user is-enabled brightness-ctl    # enabled
ls -la ~/.local/bin/brightness-ctl            # symlink → src/brightness-ctl
journalctl --user -u brightness-ctl -n 30 --no-pager
```

Any Python tracebacks in the journal → **stop**, fix, rerun from 2.2.

---

## 3. UAT test plan

Each phase has a **PASS criterion**. Check them off in your head or
copy this section to a scratch file. **Any failure → stop and
diagnose**, do not blast through subsequent phases.

### Phase A: environment sanity (no camera, no hardware risk)

- [ ] `brightness-ctl status` returns a status block with color
      temp / brightness values. (Phase 2 regression — the socket works.)
- [ ] `brightness-ctl auto-status` returns:
      ```
      Auto-brightness: OFF
      Anchor:          not set
      Calibration:     not ready
        cal_min:       N/A
        cal_max:       N/A
      ```
      **Not** `Error: unknown command: auto-status`. If you see that,
      the stale daemon is still running — go back to section 2.2.
- [ ] `journalctl --user -u brightness-ctl -f` shows the daemon doing a
      periodic apply every ~30s without errors. Ctrl-C when satisfied.

### Phase B: regression — color temp & brightness hotkeys (NO camera)

Existing Phase 2 behavior that must not have regressed:

- [ ] Press `Alt+PgUp` — notification appears in under ~100ms.
      Run it 5× quickly; all three monitors brighten together
      (consistent, not staggered).
- [ ] Press `Alt+PgDn` repeatedly to dim. `brightness-ctl status`
      reflects the change. No monitor is left at a different level.
- [ ] Press `Alt+KP+` / `Alt+KP-` to warm / cool. No monitor resets to
      `6500K`; the offset accumulates as expected.
- [ ] `Alt+End` (toggle) works — disables and re-enables.
- [ ] After 1-2 minutes of rapid hotkey use: `brightness-ctl status`
      shows the final state, state.json reflects it.

### Phase C: camera resolution — THE CRITICAL PHASE

This phase validates commit `e3f8648`. **Do not skip.** The entire
auto-brightness feature is gated on this being correct.

- [ ] Manual scan sanity — run this to see what the resolver will see:
      ```bash
      python3 -c "
      import sys; sys.path.insert(0, '/home/steve/dev/brightness-ctl/src')
      from camera import scan_v4l2_devices, resolve_camera_device, CameraError
      print('SCAN:')
      for e in scan_v4l2_devices():
          print(f'  {e[\"node\"]}  {e[\"vid\"]}:{e[\"pid\"]}')
      print()
      try:
          print('RESOLVED:', resolve_camera_device(None))
      except CameraError as e:
          print('CameraError:', e)
      "
      ```
- [ ] Scan output includes **both** the Alcor (`058f:5608`) and the
      Logitech (`046d:082c`) entries.
- [ ] `RESOLVED:` line points at whatever node has VID:PID `058f:5608`
      on this boot. On the bambam-at-audit-time state that is
      `/dev/video0`, but **don't hard-check the number** — check that
      it's the Alcor one. If it's a `046d:082c` node, the fix has
      regressed; **stop**.
- [ ] Verify the blocklist refuses a Logitech hint explicitly:
      ```bash
      python3 -c "
      import sys; sys.path.insert(0, '/home/steve/dev/brightness-ctl/src')
      from camera import resolve_camera_device, scan_v4l2_devices, CameraError
      c615 = [e['node'] for e in scan_v4l2_devices() if (e['vid'], e['pid']) == ('046d', '082c')]
      if not c615:
          print('No C615 present, skipping'); sys.exit(0)
      try:
          resolve_camera_device(c615[0])
          print('FAIL: resolver did not refuse C615 at', c615[0])
          sys.exit(1)
      except CameraError as e:
          print('OK, refused:', e)
      "
      ```
      Must print `OK, refused: refusing to open /dev/videoN: ...`

### Phase D: auto-brightness enable & first capture

Now (and only now) open the camera via the daemon.

- [ ] `brightness-ctl auto-on` returns OK. Notification reads
      `Auto-brightness ON (anchor=..., cal: not ready)`.
- [ ] `journalctl --user -u brightness-ctl -n 50` shows **no**
      `CameraError`, no "refusing to open", no Python traceback.
- [ ] **Double-check the daemon is on the right device**:
      ```bash
      DPID=$(systemctl --user show --property MainPID --value brightness-ctl)
      ls -la /proc/$DPID/fd 2>/dev/null | grep -i video
      ```
      Any `/dev/video*` fd shown must be the Alcor node (as identified
      in Phase C), **never** the Logitech node. If this shows the
      Logitech, immediately `brightness-ctl auto-off` and stop.
- [ ] Wait 60-90 seconds. `brightness-ctl auto-status` still shows
      `Calibration: not ready` (expected — no history yet). No error.
- [ ] Wait ~30 minutes, then check:
      ```bash
      ls -la ~/.config/brightness-ctl/luminance-logs/
      tail -5 ~/.config/brightness-ctl/luminance-logs/luminance-*.log
      ```
      A `luminance-YYYY-MM-DD.log` file exists with at least one JSONL
      entry containing `{"timestamp": ..., "luminance": ...}` and a
      plausible value (Alcor raw Y in dim room lighting is typically
      ~4-40; in bright lighting ~80-200; 0.0 would be suspicious).

### Phase E: calibration bootstrap

Real calibration needs ≥100 readings spanning ≥10 luminance units. At
the default 1800s (30 min) log interval, that's 50 hours of real time
if you wait passively. Three options:

1. **Patient path** — let the daemon run through a normal day/night
   cycle for 2-3 days, then `brightness-ctl auto-calibrate`.
2. **Accelerated path** — temporarily lower `luminance_log_interval`
   in `config.toml` to `60`, restart the daemon, vary room lighting
   (open/close blinds, cover camera) for ~2 hours, calibrate, then
   restore to 1800.
3. **Synthetic path** — write a fake JSONL file by hand:
   ```bash
   python3 -c "
   import json, random
   from datetime import datetime, timedelta
   from pathlib import Path
   d = Path.home() / '.config/brightness-ctl/luminance-logs'
   d.mkdir(parents=True, exist_ok=True)
   t = datetime.now()
   with open(d / f'luminance-{t.strftime(\"%Y-%m-%d\")}.log', 'w') as f:
       for i in range(200):
           ts = (t - timedelta(minutes=i*10)).isoformat(timespec='seconds')
           lum = 10 + random.random() * 80
           f.write(json.dumps({'timestamp': ts, 'luminance': lum}) + '\n')
   print('wrote 200 synthetic readings')
   "
   brightness-ctl auto-calibrate
   brightness-ctl auto-status   # should now show cal_min / cal_max populated
   ```
   Use option 3 only if you want to validate the calibration + adjust
   code paths quickly; it does not validate the real sensor's response
   curve.

Recommended UAT path: **option 2** — gives real calibration in ~2
hours with some manual light variation.

Expected outcome after calibration completes (any path):

- [ ] `brightness-ctl auto-status` shows `Calibration: ready` and
      populated `cal_min` / `cal_max` values with range ≥ 10.

### Phase F: closed-loop adjustment

With calibration ready, verify the feedback loop actually moves
brightness in response to ambient light.

- [ ] Note current `sw_brightness` / `hw_brightness` from
      `brightness-ctl status`.
- [ ] Cover the camera with your hand for ~90 seconds.
      `brightness-ctl status` after — brightness should have drifted
      **downward**.
- [ ] Uncover, wait ~90 seconds. Brightness drifts back up toward the
      anchor.
- [ ] Press `Alt+PgUp` while auto is ON. `brightness-ctl auto-status`
      shows `anchor_combined` has moved upward — manual adjustment
      **re-anchors** the loop instead of being fought by it.
- [ ] `brightness-ctl auto-off` — auto loop stops, current brightness
      persists, no camera file descriptor in `/proc/$DPID/fd`.

### Phase G: reboot survival

- [ ] `brightness-ctl auto-on`, confirm it's running and healthy.
- [ ] Reboot the machine.
- [ ] After login: `systemctl --user status brightness-ctl` is active.
      `brightness-ctl auto-status` — the
      `autobrightness_enabled=true` state persisted, calibration
      persisted, daemon re-opened the camera via the same resolver
      (confirm no Logitech fd — Phase D's `/proc/$DPID/fd` check).

### Phase H: uninstall path

Do this last, and only if you want to verify `uninstall.sh` actually
works. It will stop the daemon; re-run `./install.sh` afterward to get
back to a working state.

- [ ] `./uninstall.sh` — service stopped, disabled, removed; symlink
      removed; config + luminance-logs preserved.
- [ ] `./install.sh` again — fresh service, calibration still intact
      because logs were preserved.

---

## 4. Diagnostics if something fails

**Daemon won't start / keeps restarting:**

```bash
systemctl --user status brightness-ctl
journalctl --user -u brightness-ctl -n 100 --no-pager
```

Most likely causes:
- Missing `ddcutil` or `gammastep` (should be present on bambam — check
  `which ddcutil gammastep`).
- Python import error (tests should have caught this — did you skip
  `pytest tests/ -q` in section 0?).

**Auto-brightness reports "no allowed camera found":**

Either the Alcor isn't plugged in, isn't enumerated, or the kernel
driver failed to bind. Check:

```bash
lsusb | grep -i alcor
dmesg | tail -30 | grep -iE 'uvc|video|alcor'
ls /sys/class/video4linux/
```

**Brightness "bounces" during closed-loop adjustment:**

Either `autobrightness_range` in config is too aggressive (default 40
out of the 0-200 combined scale), or calibration is too narrow. Widen
calibration with more varied lighting samples, or shrink the range.

**The resolver picks the wrong device anyway:**

This would be a real bug in `e3f8648`. Capture:

```bash
ls -la /sys/class/video4linux/
for v in /sys/class/video4linux/video*; do
    echo "=== $v ==="
    ls -la "$v/device"
    readlink -f "$v/device"
done
python3 -c "
import sys; sys.path.insert(0, '/home/steve/dev/brightness-ctl/src')
from camera import scan_v4l2_devices
import json; print(json.dumps(scan_v4l2_devices(), indent=2))
"
```

Paste the output into a new issue / Claude session and **do not enable
auto-brightness** until the resolver is fixed.

---

## 5. Rollback

If UAT fails catastrophically and you need to get back to a working
machine quickly:

```bash
# Stop the new daemon
cd ~/dev/brightness-ctl && ./uninstall.sh

# Reset display state to sane defaults
gammastep -P -x || true
for d in $(ddcutil detect 2>/dev/null | awk '/^Display/{print $2}'); do
    ddcutil -d $d setvcp 10 50 || true
done
```

Reverting commits should only be done if a specific bug is confirmed —
prefer to fix forward. Do **not** push any revert upstream without
talking it through.

---

## 6. Known non-blocking issues (tracked, not yet fixed)

- **`_ambient_light_loop` teardown warning** — at audit time, 3 tests
  emitted `Task was destroyed but it is pending!` during teardown on
  Python 3.12. Not reproducible on the dev VM's Python 3.14. Expected
  to still show on bambam. If it does, fix by cancelling the task in
  `Daemon.run()`'s shutdown path and awaiting its completion. Polish
  item, not a blocker.
- **Open question about `~/.local/bin/gammastep-autostart`** — a
  bash-era helper, not removed by `uninstall.sh` because I don't know
  if anything else uses it. Safe to `rm` if nothing on the system
  references it; check with
  `grep -r gammastep-autostart ~/.config ~/.local 2>/dev/null`.

---

## 7. When UAT passes

1. Run `pytest tests/ -q` one more time for luck.
2. `git push origin main` (3 commits: `0d84770`, `8905dec`, `e3f8648`).
3. Decide whether to delete this `HOST_UAT.md` from the repo or keep it
   as the canonical UAT runbook for future installs on other machines.
   Recommendation: keep it, trim the "Phase 3" branding, and turn
   section 3 into a reusable smoke-test checklist.

---

## Appendix: if you are Claude Code running on the host

You have been started in `~/dev/brightness-ctl` on `linux-bambam` by
the user and pointed at this file. Your job is to execute sections 0,
2, and 3 of this document, in order, and report the PASS/FAIL state of
each Phase A-H checkbox back to the user.

Important constraints:

- **You do not have hardware yourself.** Monitor brightness and hotkey
  latency (Phases B, F) require the human to press keys and look at
  screens. Ask the user to perform those steps and report back. Do not
  mark them PASS without confirmation.
- **Phase C is gating.** Do not run `brightness-ctl auto-on` (Phase D)
  until you have visibly confirmed in Phase C that `resolve_camera_device(None)`
  returns an Alcor (`058f:5608`) node and that the C615 blocklist
  check succeeds. If Phase C fails, stop and report — do not try to
  "fix forward" into Phase D.
- **`sudo` usage on bambam** requires GUI fingerprint auth and should
  be avoided for non-essential steps. Nothing in this UAT requires
  sudo — the systemd service is a user unit, and the device nodes are
  readable by the `video` group which `steve` is already in.
- **Never pass a C615 path to `open_camera`**, even in a test script.
  Use `resolve_camera_device(None)` and let it pick.
- **Journal is your friend** — after every action, run
  `journalctl --user -u brightness-ctl -n 30 --no-pager` and scan for
  tracebacks or `CameraError`.
- When you finish a phase, update this file (or a scratch note) with
  PASS/FAIL and any unexpected output, so the human has a record.
