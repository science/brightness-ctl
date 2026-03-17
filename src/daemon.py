"""asyncio daemon: socket server, periodic apply, command dispatch."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime
from pathlib import Path

from brightness import BrightnessState, Action, bright_up, bright_down
from color_temp import get_base_temp
from config import load_config
from hardware import MockHardwareBackend, SubprocessBackend
from notify import build_notify_cmd
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
        self.notify_id: int | None = None
        self._displays: list[dict] | None = None
        self._debounce_handle: asyncio.TimerHandle | None = None

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
        """Set hardware brightness on all displays sequentially."""
        displays = self._displays or self.backend.detect_displays()
        for d in displays:
            if isinstance(self.backend, SubprocessBackend):
                await self.backend.async_set_hw_brightness(d["id"], self.state.hw_brightness)
            else:
                self.backend.set_hw_brightness(d["id"], self.state.hw_brightness)

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
            self._save_state()
            if action == Action.APPLY_SW:
                await self.apply()
                sw_dec = self._sw_to_decimal(self.state.sw_brightness)
                if self.state.sw_brightness < 100:
                    await self._notify(f"Brightness: HW 0% + SW {sw_dec:.2f}")
                else:
                    await self._notify(f"Brightness: HW {self.state.hw_brightness}%")
            elif action == Action.APPLY_HW:
                await self.apply_hw_brightness()
                await self._notify(f"Brightness: HW {self.state.hw_brightness}%")
            else:
                await self._notify("Brightness: maximum (HW 100%)")
            return {"status": "ok", "sw_brightness": self.state.sw_brightness,
                    "hw_brightness": self.state.hw_brightness}

        elif cmd == "bright-down":
            bs = BrightnessState(self.state.sw_brightness, self.state.hw_brightness)
            new_bs, action = bright_down(bs, self.config)
            self.state.sw_brightness = new_bs.sw_brightness
            self.state.hw_brightness = new_bs.hw_brightness
            self._save_state()
            if action == Action.APPLY_HW:
                await self.apply_hw_brightness()
                await self._notify(f"Brightness: HW {self.state.hw_brightness}%")
            elif action == Action.APPLY_SW:
                await self.apply()
                sw_dec = self._sw_to_decimal(self.state.sw_brightness)
                await self._notify(f"Brightness: HW 0% + SW {sw_dec:.2f}")
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

        elif cmd == "stop":
            self.should_stop = True
            return {"status": "ok", "message": "stopping"}

        else:
            return {"status": "error", "message": f"unknown command: {cmd}"}

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
            server.close()
            await server.wait_closed()
            if self.socket_path.exists():
                self.socket_path.unlink()
