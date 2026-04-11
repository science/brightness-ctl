# Host-OS questions for integration testing

Run this on **bambam** (the host, not the dev VM) and paste the output back
into the Claude session so we can continue Phase 3/4 integration testing
and UAT.

## 1. Environment sanity

```bash
hostname
whoami
uname -a
python3 --version
```

## 2. Virtiofs sanity — confirm host sees the same files the VM just committed

```bash
ls -la ~/dev/brightness-ctl/ | head -10
git -C ~/dev/brightness-ctl log --oneline -6
git -C ~/dev/brightness-ctl status
# Expect to see commits 0d84770 (Phase 3) and 8905dec (Phase 4) at the top.
```

## 3. What's currently installed / running

```bash
ls -la ~/.local/bin/brightness-ctl 2>&1
ls -la ~/.local/bin/redshift-ctl 2>&1
ls -la ~/.local/bin/gammastep-autostart 2>&1
pgrep -af brightness-ctl
pgrep -af redshift-ctl
pgrep -af gammastep
```

## 4. systemd user service state

```bash
systemctl --user status brightness-ctl 2>&1 | head -20
systemctl --user is-enabled brightness-ctl 2>&1
ls -la ~/.config/systemd/user/brightness-ctl.service 2>&1
```

## 5. Config & state on host

```bash
ls -la ~/.config/brightness-ctl/ 2>&1
cat ~/.config/brightness-ctl/config.toml 2>&1
cat ~/.config/brightness-ctl/state.json 2>&1
ls -la ~/.config/brightness-ctl/luminance-logs/ 2>&1
```

## 6. Old (bash-era) leftovers to check for

```bash
ls -la ~/.config/redshift-ctl/ 2>&1
ls -la ~/.config/autostart/gammastep-indicator.desktop 2>&1
```

## 7. Hardware prerequisites

```bash
which gammastep ddcutil notify-send 2>&1
gammastep --version 2>&1 | head -3
ddcutil --version 2>&1 | head -3
ddcutil detect 2>&1 | grep -E "^(Display|   I2C bus)" | head -30
```

## 8. Camera — CRITICAL: confirm /dev/video2 is the Alcor, not the Logitech

```bash
ls -la /dev/video* 2>&1
for d in /dev/video0 /dev/video1 /dev/video2 /dev/video3; do
    [ -e "$d" ] || continue
    echo "--- $d ---"
    v4l2-ctl -d "$d" --info 2>&1 | grep -E "Card type|Bus info|Driver name" || true
done
```

**Expected:** `/dev/video2` card type should mention **"USB Camera"** and bus
info should show the Alcor vendor (058f:5608). `/dev/video0` and
`/dev/video1` should be the **Logitech HD Webcam C615** — **never touch
those**.

## 9. Unit test run on host (regression check)

```bash
cd ~/dev/brightness-ctl && pytest tests/ -q 2>&1 | tail -10
# Expect: 181 passed
```

## 10. Socket / runtime dir

```bash
echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
ls -la "$XDG_RUNTIME_DIR/brightness-ctl.sock" 2>&1
```

## 11. Cinnamon hotkey bindings (read-only check)

```bash
dconf dump /org/cinnamon/desktop/keybindings/ 2>&1 | grep -A2 -i "brightness\|redshift" | head -40
```

---

## What we're going to do with the answers

1. Confirm Phase 3 commits are live on the host disk (they should be, via virtiofs).
2. Decide whether to run `./install.sh` fresh or just `systemctl --user restart brightness-ctl` to pick up the new code.
3. Verify `/dev/video2` is definitely the cheap Alcor webcam before enabling auto-brightness — hitting the Logitech C615 meeting cam would be a safety violation per CLAUDE.md.
4. Run the smoke-test checklist (hotkey latency, monitor consistency, color stability, auto-brightness enable/calibrate/adjust).
