# brightness-ctl — Host UAT Guide

## What's done

The daemon is installed, running under systemd, and auto-brightness is
**ON** and opening the correct camera (Alcor, never the Logitech C615).
203 tests pass. All the code-level bugs found during bring-up are fixed
and committed (see §1 below for the bug list if you're curious, or
§Fresh-install reference for how to re-verify from scratch on a new
machine).

## Your UAT checklist

Five tests, in roughly this order. Only Step 1 is "do it now" —
everything else is opportunistic. Nothing on this list takes more than
a few minutes; most of the time is passive waiting.

---

### Step 1 — Hotkey regression (do this now, ~2 min)

The point: confirm that the Phase-3 daemon didn't break the existing
Phase-2 hotkey path. You sit at the keyboard, press five key combos,
and watch the monitors.

Open any window you can visibly see across all three monitors (a full
desktop is fine — you're looking for brightness/color changes, not
content), then press these in order. Each keypress should produce a
visible notification within ~100ms.

| # | Keys            | What should happen                                                                            |
|---|-----------------|-----------------------------------------------------------------------------------------------|
| 1 | `Alt+Page_Up`   | All three monitors get visibly brighter (1 step). Notification says "Brightness up" or similar. |
| 2 | `Alt+Page_Up`   | Another step brighter. Run it 3 more times. All monitors stay **in lockstep** — no monitor left behind. |
| 3 | `Alt+Page_Down` | All three monitors dim by one step. Run it 3 more times.                                     |
| 4 | `Alt+KP_Add`    | (Keypad `+`, not top-row `+`.) Screen gets **warmer** (more orange). Notification shows the new color temp. |
| 5 | `Alt+KP_Subtract` | (Keypad `-`.) Screen gets **cooler** (more blue). Run both warmer/cooler a few times — the color offset should accumulate cleanly. |
| 6 | `Alt+End`       | Gammastep turns **off** entirely (monitors jump to default 6500K-ish white). Press it again — it turns back on at whatever color temp it was at. |

After you're done, run this to confirm the state file reflects what
you did:

```bash
brightness-ctl status
```

**Pass criteria:**
- Every keypress produced a notification within ~100ms (not 1+ seconds).
- All three monitors changed together — no staggering, no monitor stuck at a different level.
- Warmer/cooler accumulated; no monitor snapped back to 6500K partway through.
- `Alt+End` toggles cleanly both directions.

**If any of that fails:** tell me what you saw. Don't keep going.

---

### Step 2 — Next time you reboot (whenever)

Just use the machine for ~5 minutes after reboot. The things to
notice:

- Do your screens look **visibly wrong** right after login? (Stuck
  at harsh white 6500K, pitch dark, flickering, one monitor
  different from the others.)
- Do the Step 1 hotkeys still work? (Press `Alt+Page_Up` once,
  notification appears, monitors get brighter.)

If both of those feel normal, reboot survival passes. **No
commands to run.** If something looks off, tell me what you saw
and I'll run diagnostics from here.

---

### Step 3 — Next time you suspend/resume (whenever)

Same idea. After you wake the machine back up, use it for ~5 minutes.
Notice:

- Do the screens look right? (Same question as Step 2.)
- Do hotkeys still work? (Press `Alt+Page_Up` once.)
- Does brightness still respond to ambient light changes? (Only
  meaningful after Step 4 finishes — skip this sub-check until then.)

If it all feels normal, suspend survival passes. If it doesn't —
typically the screens would be stuck at a wrong color temp, or
`Alt+Page_Up` would silently do nothing — tell me and I'll check
the journal from here.

---

### Step 4 — Calibration wait (passive, ~1 day)

The point: accumulate enough luminance readings for the calibrator to
compute `cal_min` / `cal_max`.

**You don't actively do anything for this one.** Just use the machine
normally for about a day. The daemon writes one reading to
`~/.config/brightness-ctl/luminance-logs/luminance-YYYY-MM-DD.log`
every 30 minutes while the system is awake. Calibration requires ≥30
readings with a range ≥ 10 luminance units. After enough time has
passed, check:

```bash
brightness-ctl auto-status
```

If `Calibration: ready` and `cal_min` / `cal_max` are populated with a
range ≥ 10, Step 4 is done — proceed to Step 5. If it still says `not
ready` after 2+ days of normal use, tell me.

**Shortcut**: if you already know your environment's luminance range
(e.g. from a previous calibration or the luminance logs), you can
skip the wait entirely:

```bash
brightness-ctl auto-set-cal 20 180    # set min and max directly
```

**Accelerated path** (optional, if you don't want to wait):
1. Edit `~/.config/brightness-ctl/config.toml`, add `luminance_log_interval = 60` (or change it to 60 if already present).
2. `systemctl --user restart brightness-ctl`.
3. Over the next hour, vary the lighting in the room: open and close blinds, turn lamps on/off, cover the Alcor lens with your hand for a minute or two, uncover it. The goal is to produce a spread of readings, not just uniform values.
4. After ~1 hour: `brightness-ctl auto-calibrate` followed by `brightness-ctl auto-status`. Should show `Calibration: ready`.
5. Put `luminance_log_interval = 1800` back in config, `systemctl --user restart brightness-ctl`.

---

### Step 5 — Closed-loop test (after Step 4, ~3 min)

The point: with calibration ready, confirm the daemon actually
moves monitor brightness when the room gets darker or lighter.

Only run this after Step 4 shows `Calibration: ready`.

**You need to know which camera is the Alcor.** Bambam has two
cameras: the **Alcor** (tiny ambient sensor, the one brightness-ctl
uses) and the **Logitech C615** (your meeting cam). Before starting
this step, look at your hardware and identify the Alcor — if you're
not sure, ask me and I'll help figure out which physical module it
is. **Do not cover the Logitech**; that's your meeting camera and
has nothing to do with this.

Once you know which camera is the Alcor:

1. **Cover** the Alcor lens with your hand or a piece of tape. Keep
   it covered for **90 seconds**.
2. Look at your monitors. Did they dim noticeably while you were
   covering the sensor?
3. **Uncover** the Alcor. Wait another 90 seconds.
4. Did the monitors brighten back up to roughly where they were?
5. With the Alcor still uncovered, press `Alt+Page_Up` once. The
   monitors should step brighter (as in Step 1) and then **stay
   there** — the daemon should treat your hotkey press as a new
   "preferred" brightness instead of immediately dragging it back
   down.

**Pass criteria:** covered → visibly dimmer, uncovered → visibly
brighter, manual hotkey sticks.

If any of that didn't happen — especially if covering the lens had
no visible effect — tell me and I'll check the luminance log and
calibration from here.

---

## That's it

Once all 5 steps pass, UAT is complete and you can tell me to push.
The remaining items (uninstall path verification, the
`_ambient_light_loop` teardown warning, the stale
`gammastep-autostart` bash script) are optional polish — they're
itemised in §6 below but none of them blocks pushing.

If anything in Steps 1–5 fails, tell me what you saw (exact command
output, exact log lines) and I'll diagnose from the dev VM.

---

# Reference material

Everything below is reference, not part of your checklist. You can
stop reading here unless (a) something in the 5 steps above broke,
(b) you want to know *why* those steps exist, or (c) you're bringing
brightness-ctl up on a different machine. Contents:

- **§0 / §2 / §3 Phases A, C, D** — full fresh-install bring-up
  procedure. Already done on bambam; rerun on new machines.
- **§1** — the four bugs the first host UAT found and fixed.
- **§4** — diagnostics for when something in Steps 1–5 above breaks.
- **§5** — rollback (uninstall the daemon, reset display state).
- **§6** — known non-blocking issues (polish items).
- **§7** — what to do when UAT passes (push, keep this doc).
- **Appendix** — notes for a Claude Code session running on the host
  and pointed at this file.

---

## 0. Preconditions & current state

**Branch:** `main`, 6 commits ahead of `origin/main`, not yet pushed.

**Relevant commits (should be visible on the host via virtiofs):**

| SHA | Summary |
|-----|---------|
| `0d84770` | Phase 3: camera-based auto-brightness |
| `8905dec` | Phase 4: install.sh writes systemd unit; uninstall.sh |
| `e3f8648` | camera: resolve ambient sensor by USB VID:PID, blocklist C615 |
| `dc3d58e` | camera: fix 64-bit ctypes ABI, filter by VIDEO_CAPTURE, tune via BRIGHTNESS |
| `80b68ae` | daemon: wire camera_brightness, surface errors, survive suspend/resume |

Verify before starting:

```bash
cd ~/dev/brightness-ctl
git log --oneline -6   # 80b68ae should be at top
git status             # should be clean (or only this file modified)
pytest tests/ -q       # expect: 203 passed
```

If `pytest` doesn't report 203 passed, **stop** and investigate before
touching the running daemon.

---

## 1. Background: the bugs this UAT was written to catch

Three bugs were found and fixed during the first host UAT. Phase C and
Phase D each exist to verify one of them against real hardware.

### Bug 1: camera selection by device-node number (fixed in `e3f8648`)

`/dev/videoN` numbers on bambam are **not stable**:

| Device        | VID:PID     | Product (this boot) |
|---------------|-------------|---------------------|
| `/dev/video0` | `058f:5608` | Alcor "USB Camera" (may be capture OR metadata — enumeration-order dependent) |
| `/dev/video1` | `058f:5608` | Alcor (the other one) |
| `/dev/video2` | `046d:082c` | **Logitech HD Webcam C615 — MEETING CAM** |
| `/dev/video3` | `046d:082c` | Logitech metadata node |

Numbers shift across reboots, USB hotplugs, and driver rebinds — we
actually hit this mid-debug when `/dev/video0` disappeared and
`/dev/video4` appeared with the Alcor capture/metadata roles shuffled.
Phase 3 originally defaulted `camera_device = "/dev/video2"`, which on
this host points at the Logitech meeting cam. Enabling auto-brightness
as originally shipped would have opened the meeting camera.

**Fix.** `src/camera.py` now selects devices by **USB VID:PID**, not
node number. `ALCOR_AMBIENT_VIDPID = ("058f", "5608")` is allowlisted;
`BLOCKED_VIDPIDS = {("046d", "082c")}` is a hard refuse list. Even if
`config.toml` sets `camera_device` explicitly to a C615 node,
`open_camera` refuses. Node numbers are never trusted as identity
anywhere in code or docs. Phase C verifies this.

### Bug 2: 64-bit ctypes ABI mismatch (fixed in `dc3d58e`)

`v4l2_buffer` was declared as 80 bytes in `src/camera.py`; the kernel
ABI on 64-bit Linux is 88. The `m` field is a union whose pointer
variant is 8 bytes wide, but only 4 bytes were reserved for it. Every
field after `m` read 4 bytes early, so `QUERYBUF` returned
`buf.length == 0` and `mmap(fd, 0, ...)` failed with `EINVAL` — and
every ioctl was silently overflowing the Python buffer by 8 bytes.
`v4l2_format` had the same class of bug at the `type` → `fmt.pix`
boundary. Both are fixed, with `assert ctypes.sizeof(...)` guards at
module load. Phase D verifies the fix by actually grabbing a frame.

### Bug 3: metadata-node-vs-capture-node enumeration race (fixed in `dc3d58e`)

The Alcor 058f:5608 exposes **two** v4l2 nodes with the same VID:PID:
one `V4L2_CAP_VIDEO_CAPTURE` (the real camera), one `V4L2_CAP_META_CAPTURE`
(metadata-only, can't `REQBUFS` as `VIDEO_CAPTURE`). Which one gets the
lower `/dev/videoN` number depends on uvcvideo's probe order and is not
stable. The VID:PID resolver was picking whichever came first
alphabetically, which rolled the dice between "works" and "blows up in
REQBUFS". Fixed by adding `probe_has_video_capture()` (runs `QUERYCAP`
and checks the cap bit) and filtering through it in
`resolve_camera_device(capture_check=...)`. Phase D implicitly verifies
this by confirming the daemon actually holds a working fd.

### Bug 4 (minor): silent swallow of `open_camera` errors (fixed in `80b68ae`)

`_ambient_light_loop` used to catch `(OSError, CameraError)` from
`open_camera` and silently `return`, which made the bugs above
invisible for weeks — `auto-status` said `ON` while the loop had
already died. The loop now logs to stderr (→ journal), sends a
notify-send, and flips `autobrightness_enabled` back to `False` in
state so `auto-status` matches reality.

---

## 2. Pre-UAT host cleanup (do this in order)

> **On bambam this section is already done.** It is retained as a
> reference for fresh installs on other machines, or for re-running
> after something has gone badly wrong. On a healthy bambam,
> `~/.local/bin/brightness-ctl` is already symlinked, the systemd
> unit is installed and active, bash-era leftovers are gone, and
> `pkill` would kill a working daemon — skip straight to Phase B.

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

On bambam as of commit `80b68ae`, `camera_brightness = 32` is set in
`~/.config/brightness-ctl/config.toml` because the Alcor 058f:5608
module exposes only `V4L2_CID_BRIGHTNESS` (no gain/exposure controls)
and at the factory default of 0 produces near-black frames
(Y_mean ≈ 0.01). At +32 the mid-tone lands around Y≈63.

- [ ] `brightness-ctl auto-on` returns exit 0. `brightness-ctl
      auto-status` shows `ON`, anchor populated.
- [ ] `journalctl --user -u brightness-ctl -n 50` shows **no**
      `CameraError`, no "refusing to open", no "reopening", no Python
      traceback.
- [ ] **Double-check the daemon is on the right device**:
      ```bash
      DPID=$(systemctl --user show --property MainPID --value brightness-ctl)
      ls -la /proc/$DPID/fd 2>/dev/null | grep -i video
      ```
      Expect **3 fds** on the same Alcor capture node (the fd plus two
      mmap buffers — `NUM_BUFFERS = 2`). The node number is **whatever
      uvcvideo assigned this boot** — do not hard-check for a specific
      number. What matters is:
      1. All three fds point at the same node.
      2. That node's VID:PID is `058f:5608` (Alcor), **not** `046d:082c`
         (Logitech). Cross-check with:
         ```bash
         NODE=$(ls -la /proc/$DPID/fd 2>/dev/null | grep -o '/dev/video[0-9]*' | head -1)
         udevadm info --query=property --name="$NODE" | grep -E "ID_(VENDOR|MODEL)_ID"
         ```
         Must print `ID_VENDOR_ID=058f` and `ID_MODEL_ID=5608`. Any
         other pair → `brightness-ctl auto-off` immediately and stop.
      3. No fds anywhere on a node whose VID:PID is `046d:082c`.
- [ ] Wait ~65 seconds for the first ambient-loop tick, then check:
      ```bash
      ls -la ~/.config/brightness-ctl/luminance-logs/
      cat ~/.config/brightness-ctl/luminance-logs/luminance-*.log
      ```
      Expect `luminance-YYYY-MM-DD.log` with at least one JSONL entry:
      ```
      {"timestamp": "2026-04-10T22:27:29", "luminance": 63.0}
      ```
      On this host with `camera_brightness=32` the value should be
      **around 60–80** in typical indoor lighting. A reading of exactly
      `0.0` means the sensor is seeing nothing — possible but suspicious;
      verify the Alcor isn't physically covered or disconnected. A
      reading of exactly `255.0` means saturation — lower
      `camera_brightness` in config. First entry is logged immediately on
      first successful capture; subsequent entries are throttled to
      `luminance_log_interval` (default 1800s / 30min).

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

### Phase G: reboot and suspend/resume survival

#### G.1 — reboot

- [ ] `brightness-ctl auto-on`, confirm it's running and healthy.
- [ ] Reboot the machine.
- [ ] After login: `systemctl --user status brightness-ctl` is active.
      `brightness-ctl auto-status` — the `autobrightness_enabled=true`
      state persisted, calibration persisted (if it was ready before
      the reboot), daemon re-opened the camera via the same resolver
      (re-run Phase D's `/proc/$DPID/fd` check — three fds on an Alcor
      capture node, no Logitech).

#### G.2 — suspend / resume

`systemd-suspend` invalidates open V4L2 fds when the USB bus goes down
and the camera re-enumerates on resume. The daemon is supposed to
detect the stale fd, close it, re-resolve the device, reopen, and
continue — see `_ambient_light_loop`'s reopen branch in `daemon.py`
and commit `80b68ae` for the rationale.

- [ ] Confirm `brightness-ctl auto-status` is `ON` before suspending.
- [ ] Note the current video fd(s) held by the daemon:
      ```bash
      DPID=$(systemctl --user show --property MainPID --value brightness-ctl)
      ls -la /proc/$DPID/fd 2>/dev/null | grep video
      ```
- [ ] Suspend (lid close, `systemctl suspend`, or Cinnamon menu).
      Wait at least 30 seconds so it's a real suspend, not a quick
      flicker.
- [ ] Resume.
- [ ] Within about 60 seconds (one `autobrightness_interval`), the
      daemon should be back on a working Alcor capture node:
      ```bash
      journalctl --user -u brightness-ctl --since "2 minutes ago" \
          --no-pager | grep -iE "reopen|camera"
      ```
      Expect at most one line of the form
      `brightness-ctl: camera read failed, reopening: ...` followed by
      silence. **No** lines saying `reopen still failing` repeatedly.
      If you see `camera open failed` / `autobrightness_enabled=False`,
      the reopen branch is broken — capture the journal output and
      stop.
- [ ] Re-run the `/proc/$DPID/fd` check from Phase D. Three fds on an
      Alcor capture node (possibly a different `/dev/videoN` than
      before the suspend — that's fine and is what the resolver is
      there to handle).
- [ ] New luminance-log entries accumulate after resume at the normal
      cadence (`luminance_log_interval`, default 1800s).

If the suspend is long enough to cover a scheduled log-write interval
there will just be a gap in the log file — calibration tolerates that
because it works over a 7-day rolling window, not contiguous samples.

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

This would be a real bug in `e3f8648` / `dc3d58e`. Capture:

```bash
ls -la /sys/class/video4linux/
for v in /sys/class/video4linux/video*; do
    echo "=== $v ==="
    ls -la "$v/device"
    readlink -f "$v/device"
done
python3 -c "
import sys; sys.path.insert(0, '/home/steve/dev/brightness-ctl/src')
from camera import (scan_v4l2_devices, probe_has_video_capture,
                    resolve_camera_device)
import json
entries = scan_v4l2_devices()
for e in entries:
    e['has_capture'] = probe_has_video_capture(e['node'])
print(json.dumps(entries, indent=2))
print('RESOLVED:', resolve_camera_device(None, capture_check=probe_has_video_capture))
"
```

Paste the output into a new issue / Claude session and **do not enable
auto-brightness** until the resolver is fixed.

**Luminance readings are stuck at 0.0 or very small:**

The Alcor 058f:5608 module exposes only `V4L2_CID_BRIGHTNESS` and at
the default 0 produces near-black frames. Check `camera_brightness` in
`~/.config/brightness-ctl/config.toml` — it should be `32` on bambam.
If you've wiped the config, Phase D's luminance sanity check will
flag this.

**The daemon keeps logging "camera read failed, reopening":**

If this happens once after a suspend/resume, it's the intended
suspend-recovery path (see Phase G.2). If it repeats continuously,
the camera is either unplugged, the uvcvideo driver has lost state,
or the retry code is broken. Check `lsusb | grep -i alcor` and the
output of the `probe_has_video_capture` snippet above.

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

- **`_ambient_light_loop` teardown warning** — three tests emit
  `Task was destroyed but it is pending!` during teardown on
  Python 3.12. Still reproduces on bambam. Fix is to cancel the task
  in `Daemon.run()`'s shutdown path and await its completion. Polish
  item, not a blocker.
- **`~/.local/bin/gammastep-autostart` is still on disk** — a bash-era
  helper not removed by `uninstall.sh` because the original audit
  wasn't sure if anything else referenced it. Safe to `rm` if nothing
  on the system references it; check with
  `grep -r gammastep-autostart ~/.config ~/.local 2>/dev/null`.

---

## 7. When UAT passes

1. Run `pytest tests/ -q` one more time for luck. Expect 203 passed.
2. `git push origin main` (6 commits: `0d84770`, `8905dec`, `e3f8648`,
   `06c9c21`, `dc3d58e`, `80b68ae`).
3. Keep this `HOST_UAT.md` in the repo as the canonical install/UAT
   runbook for future machines. On each fresh install, sections 0, 2,
   and Phases A/C/D are the actual bring-up; Phases B/E/F/G/H are the
   acceptance checks.

---

## Appendix: if you are Claude Code running on the host

You have been started in `~/dev/brightness-ctl` on `linux-bambam` by
the user and pointed at this file. **Check "Current status" at the top
first** — on a healthy bambam, sections 0, 2, and Phases A/C/D are
already done and your job reduces to helping Steve work through the
remaining phases (B, E, F, G, H). On a fresh machine, work top to
bottom.

Important constraints:

- **You do not have hardware yourself.** Monitor brightness and hotkey
  latency (Phases B, F) require the human to press keys and look at
  screens. Ask the user to perform those steps and report back. Do not
  mark them PASS without confirmation.
- **Suspend/resume (Phase G.2) also requires the human** — only they
  can close the lid or invoke `systemctl suspend`. You can prepare the
  check (record pre-suspend fds), but they have to trigger the suspend
  and come back.
- **Phase C is gating.** Do not run `brightness-ctl auto-on` (Phase D)
  until you have visibly confirmed in Phase C that
  `resolve_camera_device(None, capture_check=probe_has_video_capture)`
  returns an Alcor (`058f:5608`) node and that the C615 blocklist
  check succeeds. If Phase C fails, stop and report — do not try to
  "fix forward" into Phase D.
- **`sudo` usage on bambam** requires GUI fingerprint auth and should
  be avoided for non-essential steps. Nothing in this UAT requires
  sudo — the systemd service is a user unit, and the device nodes are
  readable by the `video` group which `steve` is already in.
- **Never pass a C615 path to `open_camera`**, even in a test script.
  Use `resolve_camera_device(None, capture_check=probe_has_video_capture)`
  and let it pick.
- **`/proc/$DPID/fd` is the ground truth** for which camera the daemon
  actually opened. `auto-status: ON` by itself is not sufficient —
  before commit `80b68ae` the status was a lie when the camera failed
  to open. Always follow `auto-on` with the fd check from Phase D.
- **Journal is your friend** — after every action, run
  `journalctl --user -u brightness-ctl -n 30 --no-pager` and scan for
  tracebacks, `CameraError`, or `reopening` messages.
- When you finish a phase, update this file (or a scratch note) with
  PASS/FAIL and any unexpected output, so the human has a record.
