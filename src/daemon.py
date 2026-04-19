"""asyncio daemon: socket server, periodic apply, command dispatch."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime
from pathlib import Path

from autobrightness import to_combined, from_combined, calibration_ready, compute_ambient_pct, compute_anchor, compute_target
from brightness import BrightnessState, Action, bright_up, bright_down
from color_temp import get_base_temp
from config import load_config
from hardware import MockHardwareBackend, SubprocessBackend
from luminance_log import append_reading, load_readings, compute_calibration, rotate_logs
from notify import build_notify_cmd, load_notify_id, save_notify_id
from screensaver import ScreensaverWatcher
from state import AppState, load_state, save_state


class Daemon:
    """brightness-ctl daemon — single asyncio event loop."""

    def __init__(
        self,
        config_path: Path,
        state_path: Path,
        socket_path: Path,
        backend=None,
    ):
        self.config_path = config_path
        self.state_path = state_path
        self.socket_path = socket_path
        self.config = load_config(config_path)
        self.state = load_state(state_path)
        self.backend = backend or SubprocessBackend()
        self.should_stop = False
        self.notify_id: int | None = load_notify_id()
        self._displays: list[dict] | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None
        self.log_dir = config_path.parent / "luminance-logs"
        self._camera_handle = None
        self._ambient_task: asyncio.Task | None = None
        self._last_ambient_pct: float | None = None
        self._last_log_rotation: datetime | None = None
        self._last_luminance_log: datetime | None = None
        self._screensaver_task: asyncio.Task | None = None
        self._screensaver_watcher: ScreensaverWatcher | None = None

    def _save_state(self) -> None:
        save_state(self.state, self.state_path)

    def _clamp(self, val: int, lo: int, hi: int) -> int:
        return max(lo, min(val, hi))

    def _get_applied_temp(self) -> tuple[int, int]:
        """Return (base_temp, applied_temp)."""
        now = datetime.now()
        base = get_base_temp(now.hour, now.minute, self.config)
        applied = base + self.state.offset
        applied = self._clamp(applied, self.config["min_temp"], self.config["max_temp"])
        return base, applied

    def _sw_to_decimal(self, sw: int) -> float:
        return sw / 100.0

    async def apply(self) -> None:
        """Apply current color temperature + software brightness."""
        if not self.state.enabled:
            if isinstance(self.backend, SubprocessBackend):
                await self.backend.async_reset_color_temp(self.config["method"])
            else:
                self.backend.reset_color_temp(self.config["method"])
            return
        _, applied = self._get_applied_temp()
        sw_dec = self._sw_to_decimal(self.state.sw_brightness)
        if isinstance(self.backend, SubprocessBackend):
            await self.backend.async_apply_color_temp(applied, sw_dec, self.config["method"])
        else:
            self.backend.apply_color_temp(applied, sw_dec, self.config["method"])

    async def apply_hw_brightness(self) -> None:
        """Set hardware brightness on all displays in parallel (by bus ID)."""
        displays = self._displays or self.backend.detect_displays()
        if isinstance(self.backend, SubprocessBackend):
            await self.backend.async_set_hw_brightness_parallel(displays, self.state.hw_brightness)
        else:
            for d in displays:
                self.backend.set_hw_brightness(d["id"], self.state.hw_brightness)

    def apply_hw_brightness_background(self) -> None:
        """Fire-and-forget HW brightness apply (notification sent first)."""
        asyncio.ensure_future(self.apply_hw_brightness())

    async def _notify(self, msg: str) -> None:
        """Send desktop notification."""
        cmd = build_notify_cmd(msg, self.notify_id)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            new_id = stdout.decode().strip()
            if new_id.isdigit():
                self.notify_id = int(new_id)
                save_notify_id(self.notify_id)
        except OSError:
            pass

    async def handle_command(self, request: dict) -> dict:
        """Dispatch a command and return a response dict."""
        cmd = request.get("cmd", "")

        if cmd == "warmer":
            if not self.state.enabled:
                self.state.enabled = True
            self.state.offset -= self.config["step"]
            base, applied = self._get_applied_temp()
            self.state.offset = applied - base
            self._save_state()
            await self.apply()
            await self._notify(
                f"Warmer: {applied}K  (base {base}K, offset {self.state.offset}K)"
            )
            return {"status": "ok", "applied_temp": applied, "offset": self.state.offset}

        elif cmd == "cooler":
            if not self.state.enabled:
                self.state.enabled = True
            self.state.offset += self.config["step"]
            base, applied = self._get_applied_temp()
            self.state.offset = applied - base
            self._save_state()
            await self.apply()
            await self._notify(
                f"Cooler: {applied}K  (base {base}K, offset {self.state.offset}K)"
            )
            return {"status": "ok", "applied_temp": applied, "offset": self.state.offset}

        elif cmd == "toggle":
            self.state.enabled = not self.state.enabled
            self._save_state()
            await self.apply()
            if self.state.enabled:
                _, applied = self._get_applied_temp()
                await self._notify(f"ENABLED: {applied}K")
            else:
                await self._notify("DISABLED (neutral 6500K)")
            return {"status": "ok", "enabled": self.state.enabled}

        elif cmd == "reset":
            self.state.offset = 0
            self.state.enabled = True
            self._save_state()
            await self.apply()
            base, _ = self._get_applied_temp()
            await self._notify(f"RESET: {base}K (no offset)")
            return {"status": "ok", "offset": 0}

        elif cmd == "bright-up":
            bs = BrightnessState(self.state.sw_brightness, self.state.hw_brightness)
            new_bs, action = bright_up(bs, self.config)
            self.state.sw_brightness = new_bs.sw_brightness
            self.state.hw_brightness = new_bs.hw_brightness
            if self.state.autobrightness_enabled:
                new_combined = to_combined(
                    self.state.sw_brightness, self.state.hw_brightness,
                    sw_min=self.config["sw_min"],
                )
                if self._last_ambient_pct is not None:
                    self.state.anchor_combined = compute_anchor(
                        new_combined, self._last_ambient_pct,
                        self.config["autobrightness_range"],
                    )
                else:
                    self.state.anchor_combined = new_combined
            self._save_state()
            if action == Action.APPLY_SW:
                sw_dec = self._sw_to_decimal(self.state.sw_brightness)
                if self.state.sw_brightness < 100:
                    await self._notify(f"Brightness: HW 0% + SW {sw_dec:.2f}")
                else:
                    await self._notify(f"Brightness: HW {self.state.hw_brightness}%")
                await self.apply()
            elif action == Action.APPLY_HW:
                await self._notify(f"Brightness: HW {self.state.hw_brightness}%")
                self.apply_hw_brightness_background()
            else:
                await self._notify("Brightness: maximum (HW 100%)")
            return {"status": "ok", "sw_brightness": self.state.sw_brightness,
                    "hw_brightness": self.state.hw_brightness}

        elif cmd == "bright-down":
            bs = BrightnessState(self.state.sw_brightness, self.state.hw_brightness)
            new_bs, action = bright_down(bs, self.config)
            self.state.sw_brightness = new_bs.sw_brightness
            self.state.hw_brightness = new_bs.hw_brightness
            if self.state.autobrightness_enabled:
                new_combined = to_combined(
                    self.state.sw_brightness, self.state.hw_brightness,
                    sw_min=self.config["sw_min"],
                )
                if self._last_ambient_pct is not None:
                    self.state.anchor_combined = compute_anchor(
                        new_combined, self._last_ambient_pct,
                        self.config["autobrightness_range"],
                    )
                else:
                    self.state.anchor_combined = new_combined
            self._save_state()
            if action == Action.APPLY_HW:
                await self._notify(f"Brightness: HW {self.state.hw_brightness}%")
                self.apply_hw_brightness_background()
            elif action == Action.APPLY_SW:
                sw_dec = self._sw_to_decimal(self.state.sw_brightness)
                await self._notify(f"Brightness: HW 0% + SW {sw_dec:.2f}")
                await self.apply()
            else:
                sw_dec = self._sw_to_decimal(self.config["sw_min"])
                await self._notify(f"Brightness: minimum (HW 0% + SW {sw_dec:.2f})")
            return {"status": "ok", "sw_brightness": self.state.sw_brightness,
                    "hw_brightness": self.state.hw_brightness}

        elif cmd == "status":
            base, applied = self._get_applied_temp()
            return {
                "status": "ok",
                "enabled": self.state.enabled,
                "base_temp": base,
                "applied_temp": applied,
                "offset": self.state.offset,
                "sw_brightness": self.state.sw_brightness,
                "hw_brightness": self.state.hw_brightness,
                "day_temp": self.config["day_temp"],
                "night_temp": self.config["night_temp"],
            }

        elif cmd == "auto-on":
            return await self._handle_auto_on()

        elif cmd == "auto-off":
            return await self._handle_auto_off()

        elif cmd == "auto-status":
            return self._handle_auto_status()

        elif cmd == "auto-calibrate":
            return self._handle_auto_calibrate()

        elif cmd == "auto-reset-cal":
            return self._handle_auto_reset_cal()

        elif cmd == "auto-set-cal":
            return self._handle_auto_set_cal(request.get("args", {}))

        elif cmd == "stop":
            self.should_stop = True
            return {"status": "ok", "message": "stopping"}

        else:
            return {"status": "error", "message": f"unknown command: {cmd}"}

    async def _handle_auto_on(self) -> dict:
        """Enable autobrightness, set anchor from current brightness."""
        sw_min = self.config["sw_min"]
        self.state.anchor_combined = to_combined(
            self.state.sw_brightness, self.state.hw_brightness, sw_min=sw_min,
        )
        self.state.autobrightness_enabled = True

        # Run calibration from logs
        self._run_calibration()

        self._save_state()
        self._start_ambient_task()

        cal_status = "ready" if calibration_ready(self.state.cal_min, self.state.cal_max) else "not ready"
        await self._notify(f"Auto-brightness ON (anchor={self.state.anchor_combined:.0f}, cal: {cal_status})")
        return {
            "status": "ok",
            "autobrightness_enabled": True,
            "anchor_combined": self.state.anchor_combined,
            "cal_min": self.state.cal_min,
            "cal_max": self.state.cal_max,
        }

    async def _handle_auto_off(self) -> dict:
        """Disable autobrightness, keep current applied brightness."""
        self.state.autobrightness_enabled = False
        self._stop_ambient_task()
        self._save_state()
        await self._notify("Auto-brightness OFF")
        return {"status": "ok", "autobrightness_enabled": False}

    def _handle_auto_status(self) -> dict:
        """Show autobrightness status."""
        cal_ok = calibration_ready(self.state.cal_min, self.state.cal_max)
        return {
            "status": "ok",
            "autobrightness_enabled": self.state.autobrightness_enabled,
            "anchor_combined": self.state.anchor_combined,
            "cal_min": self.state.cal_min,
            "cal_max": self.state.cal_max,
            "calibration_ready": cal_ok,
        }

    def _handle_auto_calibrate(self) -> dict:
        """Recompute calibration from luminance logs."""
        self._run_calibration()
        self._save_state()
        cal_ok = calibration_ready(self.state.cal_min, self.state.cal_max)
        return {
            "status": "ok",
            "autobrightness_enabled": self.state.autobrightness_enabled,
            "anchor_combined": self.state.anchor_combined,
            "cal_min": self.state.cal_min,
            "cal_max": self.state.cal_max,
            "calibration_ready": cal_ok,
        }

    def _handle_auto_reset_cal(self) -> dict:
        """Clear calibration and delete luminance logs."""
        self.state.cal_min = None
        self.state.cal_max = None
        self._save_state()
        # Delete all luminance logs
        if self.log_dir.exists():
            for f in self.log_dir.glob("luminance-*.log"):
                f.unlink()
        return {"status": "ok", "message": "calibration and logs cleared"}

    def _handle_auto_set_cal(self, args: dict) -> dict:
        try:
            cal_min = float(args["cal_min"])
            cal_max = float(args["cal_max"])
        except (KeyError, TypeError, ValueError):
            return {"status": "error", "message": "requires cal_min and cal_max (numeric)"}
        if cal_min >= cal_max:
            return {"status": "error", "message": "cal_min must be less than cal_max"}
        if cal_max - cal_min < 10:
            return {"status": "error", "message": "calibration range must be >= 10"}
        self.state.cal_min = cal_min
        self.state.cal_max = cal_max
        self._save_state()
        cal_ok = calibration_ready(self.state.cal_min, self.state.cal_max)
        return {
            "status": "ok",
            "autobrightness_enabled": self.state.autobrightness_enabled,
            "anchor_combined": self.state.anchor_combined,
            "cal_min": self.state.cal_min,
            "cal_max": self.state.cal_max,
            "calibration_ready": cal_ok,
        }

    def _run_calibration(self) -> None:
        """Recompute calibration from luminance logs and update state."""
        readings = load_readings(
            self.log_dir, self.config["calibration_lookback_days"]
        )
        result = compute_calibration(
            readings,
            pct_lo=self.config["calibration_percentile_lo"],
            pct_hi=self.config["calibration_percentile_hi"],
        )
        if result is not None:
            self.state.cal_min, self.state.cal_max = result

    def _start_ambient_task(self) -> None:
        """Start the ambient light sensing loop."""
        self._stop_ambient_task()
        self._ambient_task = asyncio.create_task(self._ambient_light_loop())

    def _stop_ambient_task(self) -> None:
        """Cancel the ambient light task if running."""
        if self._ambient_task is not None:
            self._ambient_task.cancel()
            self._ambient_task = None

    async def _on_screensaver_active(self, active: bool) -> None:
        """ScreenSaver.ActiveChanged callback — drive DPMS state."""
        mode = self.config["screensaver_dpms_mode"] if active else "on"
        if isinstance(self.backend, SubprocessBackend):
            await self.backend.async_set_dpms(mode)
        else:
            self.backend.set_dpms(mode)

    def _start_screensaver_task(self) -> None:
        """Start the Cinnamon ScreenSaver ActiveChanged watcher."""
        self._stop_screensaver_task()
        self._screensaver_watcher = ScreensaverWatcher(
            on_active=self._on_screensaver_active,
        )
        self._screensaver_task = asyncio.create_task(
            self._screensaver_watcher.run()
        )

    def _stop_screensaver_task(self) -> None:
        """Cancel the screensaver watcher task if running."""
        if self._screensaver_task is not None:
            self._screensaver_task.cancel()
            self._screensaver_task = None
        self._screensaver_watcher = None

    async def _ambient_light_loop(self) -> None:
        """Periodic camera read + auto-brightness adjustment."""
        from camera import (
            CameraError, capture_luminance, close_camera, open_camera,
            probe_has_video_capture, resolve_camera_device,
        )

        import functools
        import sys

        interval = self.config["autobrightness_interval"]
        hint = self.config.get("camera_device")
        num_frames = self.config["camera_frames"]
        brightness = self.config.get("camera_brightness")
        handle = None

        resolve = functools.partial(
            resolve_camera_device, capture_check=probe_has_video_capture,
        )

        loop = asyncio.get_event_loop()

        # Resolve the safe device by USB VID:PID + VIDEO_CAPTURE capability.
        # Refuses blocklisted nodes (e.g. the Logitech C615 meeting cam)
        # even if set in config, and skips metadata-only v4l2 nodes that
        # share a VID:PID with the real capture node.
        try:
            device = await loop.run_in_executor(None, resolve, hint)
        except CameraError as e:
            print(f"brightness-ctl: auto-brightness camera resolve failed: {e}",
                  file=sys.stderr, flush=True)
            await self._notify(f"Auto-brightness: camera error — {e}")
            self.state.autobrightness_enabled = False
            self._save_state()
            return

        try:
            try:
                handle = await loop.run_in_executor(
                    None,
                    functools.partial(open_camera, device, brightness=brightness),
                )
            except (OSError, CameraError) as e:
                # Surface this failure — silently returning makes a
                # misbehaving camera indistinguishable from a working one
                # and hides real bugs (e.g. kernel ABI mismatches) behind
                # an apparently-healthy `auto-status: ON`.
                print(f"brightness-ctl: auto-brightness open_camera failed: {e}",
                      file=sys.stderr, flush=True)
                await self._notify(f"Auto-brightness: camera open failed — {e}")
                self.state.autobrightness_enabled = False
                self._save_state()
                return

            while not self.should_stop and self.state.autobrightness_enabled:
                # If a previous iteration's reopen attempt failed we come
                # back here with handle=None. Try again before capturing.
                if handle is None:
                    try:
                        device = await loop.run_in_executor(None, resolve, hint)
                        handle = await loop.run_in_executor(
                            None,
                            functools.partial(
                                open_camera, device, brightness=brightness,
                            ),
                        )
                    except (OSError, CameraError) as e:
                        print(f"brightness-ctl: camera reopen still failing, "
                              f"retry in {interval}s: {e}",
                              file=sys.stderr, flush=True)
                        await asyncio.sleep(interval)
                        continue

                try:
                    luminance = await loop.run_in_executor(
                        None, capture_luminance, handle, num_frames,
                    )
                except OSError as e:
                    # Likely a suspend/resume or USB hotplug — the fd is
                    # stale. Close it, try to reopen, and keep the loop
                    # alive. If the reopen fails we sleep and retry next
                    # tick rather than die, because a laptop lid might be
                    # closed right now and the camera will come back later.
                    print(f"brightness-ctl: camera read failed, reopening: {e}",
                          file=sys.stderr, flush=True)
                    try:
                        await loop.run_in_executor(None, close_camera, handle)
                    except Exception:
                        pass
                    handle = None
                    try:
                        device = await loop.run_in_executor(None, resolve, hint)
                        handle = await loop.run_in_executor(
                            None,
                            functools.partial(
                                open_camera, device, brightness=brightness,
                            ),
                        )
                    except (OSError, CameraError) as e2:
                        print(f"brightness-ctl: camera reopen failed, will "
                              f"retry in {interval}s: {e2}",
                              file=sys.stderr, flush=True)
                    await asyncio.sleep(interval)
                    continue

                # Log reading (throttled to luminance_log_interval)
                now = datetime.now()
                log_interval = self.config["luminance_log_interval"]
                if (
                    self._last_luminance_log is None
                    or (now - self._last_luminance_log).total_seconds() >= log_interval
                ):
                    append_reading(self.log_dir, luminance)
                    self._last_luminance_log = now

                # Apply auto-adjustment if calibrated
                if calibration_ready(self.state.cal_min, self.state.cal_max):
                    ambient_pct = compute_ambient_pct(
                        luminance, self.state.cal_min, self.state.cal_max,
                    )
                    self._last_ambient_pct = ambient_pct
                    target = compute_target(
                        self.state.anchor_combined, ambient_pct,
                        self.config["autobrightness_range"],
                    )
                    sw, hw = from_combined(target, sw_min=self.config["sw_min"])

                    if sw != self.state.sw_brightness or hw != self.state.hw_brightness:
                        self.state.sw_brightness = sw
                        self.state.hw_brightness = hw
                        self._save_state()
                        await self.apply()
                        await self.apply_hw_brightness()

                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        finally:
            if handle is not None:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, close_camera, handle)
                except Exception:
                    pass

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single client connection on the Unix socket."""
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not data:
                return
            try:
                request = json.loads(data.decode())
            except json.JSONDecodeError:
                response = {"status": "error", "message": "invalid JSON"}
                writer.write(json.dumps(response).encode() + b"\n")
                await writer.drain()
                return

            response = await self.handle_command(request)
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        except asyncio.TimeoutError:
            pass
        except ConnectionResetError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass

    async def periodic_apply(self, interval: float = 30.0) -> None:
        """Periodically re-apply color temperature for time-based transitions."""
        while not self.should_stop:
            await self.apply()
            # Daily log rotation (cheap check)
            now = datetime.now()
            if (
                self._last_log_rotation is None
                or (now - self._last_log_rotation).total_seconds() >= 86400
            ):
                rotate_logs(self.log_dir, self.config["log_retention_days"])
                self._last_log_rotation = now
            await asyncio.sleep(interval)

    async def run(self) -> None:
        """Main daemon entry point."""
        # Detect displays at startup
        if isinstance(self.backend, SubprocessBackend):
            self._displays = await self.backend.async_detect_displays()
        else:
            self._displays = self.backend.detect_displays()

        # Remove stale socket
        if self.socket_path.exists():
            self.socket_path.unlink()

        server = await asyncio.start_unix_server(
            self.handle_client, path=str(self.socket_path)
        )

        # Initial apply
        await self.apply()

        # Start periodic apply
        periodic_task = asyncio.create_task(self.periodic_apply())

        # Resume ambient light task if autobrightness was enabled
        if self.state.autobrightness_enabled:
            self._start_ambient_task()

        # Start screensaver→DPMS watcher if enabled
        if self.config["screensaver_monitor_off"]:
            self._start_screensaver_task()

        # Wait until stop is requested
        try:
            while not self.should_stop:
                await asyncio.sleep(0.5)
        finally:
            periodic_task.cancel()
            try:
                await periodic_task
            except asyncio.CancelledError:
                pass
            self._stop_screensaver_task()
            server.close()
            await server.wait_closed()
            if self.socket_path.exists():
                self.socket_path.unlink()
