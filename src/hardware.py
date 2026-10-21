"""HardwareBackend protocol + SubprocessBackend + MockHardwareBackend."""

from __future__ import annotations

import asyncio
import re
from typing import Protocol


def parse_dpms_state(xset_q: str) -> str | None:
    """Return the DPMS level ("On", "Standby", "Suspend", "Off") from `xset q`.

    None when the output has no "Monitor is ..." line (DPMS disabled, or
    xset couldn't reach the display).
    """
    m = re.search(r"Monitor is (?:in )?(\w+)", xset_q)
    return m.group(1) if m else None


def parse_idle_ms(xprintidle_out: str) -> int | None:
    """Return X input idle time in ms from `xprintidle` output, or None."""
    try:
        return int(xprintidle_out.split()[0])
    except (IndexError, ValueError):
        return None


class HardwareBackend(Protocol):
    """Protocol for hardware interaction (gammastep, ddcutil)."""

    def detect_displays(self) -> list[dict]:
        ...

    def apply_color_temp(self, temp: int, brightness: float, method: str) -> None:
        ...

    def reset_color_temp(self, method: str) -> None:
        ...

    def set_hw_brightness(self, display_id: int, value: int) -> None:
        ...

    def set_dpms(self, state: str) -> None:
        ...

    def query_idle_ms(self) -> int | None:
        ...

    def query_dpms_state(self) -> str | None:
        ...


class MockHardwareBackend:
    """Records call log for test assertions."""

    def __init__(self, num_displays: int = 3):
        self._num_displays = num_displays
        self.calls: list[tuple] = []
        # Canned answers for the screensaver re-blank check.
        self.idle_ms: int | None = 0
        self.dpms_state: str | None = "On"

    def detect_displays(self) -> list[dict]:
        return [{"id": i + 1, "bus": i + 3} for i in range(self._num_displays)]

    def apply_color_temp(self, temp: int, brightness: float, method: str) -> None:
        self.calls.append(("apply_color_temp", temp, brightness, method))

    def reset_color_temp(self, method: str) -> None:
        self.calls.append(("reset_color_temp", method))

    def set_hw_brightness(self, display_id: int, value: int) -> None:
        self.calls.append(("set_hw_brightness", display_id, value))

    def set_dpms(self, state: str) -> None:
        self.calls.append(("set_dpms", state))

    def query_idle_ms(self) -> int | None:
        return self.idle_ms

    def query_dpms_state(self) -> str | None:
        return self.dpms_state

    def clear(self) -> None:
        self.calls.clear()


class SubprocessBackend:
    """Real implementation using asyncio subprocess calls."""

    def __init__(self):
        self._displays: list[dict] | None = None
        self.last_detect_error = ""

    @staticmethod
    def _build_apply_cmd(temp: int, brightness: float, method: str) -> list[str]:
        bri = f"{brightness:.2f}"
        return ["gammastep", "-m", method, "-P", "-O", str(temp), "-b", f"{bri}:{bri}"]

    @staticmethod
    def _build_reset_cmd(method: str) -> list[str]:
        return ["gammastep", "-m", method, "-P", "-x"]

    @staticmethod
    def _build_hw_brightness_cmd(display_id: int, value: int) -> list[str]:
        return ["ddcutil", "-d", str(display_id), "setvcp", "10", str(value)]

    @staticmethod
    def _build_hw_brightness_cmd_by_bus(bus: int, value: int) -> list[str]:
        return ["ddcutil", "--bus", str(bus), "setvcp", "10", str(value), "--noverify"]

    @staticmethod
    def _build_dpms_cmd(state: str) -> list[str]:
        return ["xset", "dpms", "force", state]

    async def async_apply_color_temp(self, temp: int, brightness: float, method: str) -> None:
        cmd = self._build_apply_cmd(temp, brightness, method)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

    async def async_reset_color_temp(self, method: str) -> None:
        cmd = self._build_reset_cmd(method)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

    async def async_set_hw_brightness_parallel(
        self, displays: list[dict], value: int
    ) -> list[tuple[dict, str]]:
        """Set HW brightness on all displays in parallel using bus IDs.

        Returns (display, output) for each display whose ddcutil call exited
        non-zero; an empty list means every write succeeded.
        """
        cmds = []
        for d in displays:
            bus = d.get("bus")
            if bus is not None:
                cmds.append(self._build_hw_brightness_cmd_by_bus(bus, value))
            else:
                cmds.append(self._build_hw_brightness_cmd(d["id"], value))
        results = await asyncio.gather(*(self._run_cmd(c) for c in cmds))
        return [(d, out) for d, (rc, out) in zip(displays, results) if rc != 0]

    async def async_set_hw_brightness(self, display_id: int, value: int) -> None:
        cmd = self._build_hw_brightness_cmd(display_id, value)
        await self._run_cmd(cmd)

    async def async_set_dpms(self, state: str) -> None:
        cmd = self._build_dpms_cmd(state)
        await self._run_cmd(cmd)

    async def async_query_idle_ms(self) -> int | None:
        """X input idle time via `xprintidle`; None if it can't be read."""
        try:
            rc, out = await self._run_cmd(["xprintidle"])
        except OSError:
            return None
        return parse_idle_ms(out) if rc == 0 else None

    async def async_query_dpms_state(self) -> str | None:
        """Current DPMS level via `xset q`; None if it can't be read."""
        try:
            rc, out = await self._run_cmd(["xset", "q"])
        except OSError:
            return None
        return parse_dpms_state(out) if rc == 0 else None

    async def _run_cmd(self, cmd: list[str]) -> tuple[int, str]:
        """Run cmd; return (exit code, stripped stdout+stderr).

        Both streams are kept because ddcutil 1.4 prints errors on stdout.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await proc.communicate()
        return proc.returncode, out.decode(errors="replace").strip()

    async def async_detect_displays(self) -> list[dict]:
        proc = await asyncio.create_subprocess_exec(
            "ddcutil", "detect",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        out = stdout.decode(errors="replace")
        displays = []
        current_id = None
        for line in out.splitlines():
            if line.startswith("Display "):
                try:
                    current_id = int(line.split()[1])
                except (IndexError, ValueError):
                    current_id = None
            elif current_id is not None and "I2C bus:" in line:
                bus_match = re.search(r"/dev/i2c-(\d+)", line)
                if bus_match:
                    displays.append({"id": current_id, "bus": int(bus_match.group(1))})
                    current_id = None
        # ddcutil 1.4 explains a failed detect on stdout, so fall back to it.
        self.last_detect_error = (
            "" if displays else (stderr.decode(errors="replace").strip() or out.strip())
        )
        self._displays = displays
        return displays

    # Sync wrappers for protocol compatibility (used by mock, not by daemon)
    def detect_displays(self) -> list[dict]:
        if self._displays is not None:
            return self._displays
        return []

    def apply_color_temp(self, temp: int, brightness: float, method: str) -> None:
        pass  # Use async version in daemon

    def reset_color_temp(self, method: str) -> None:
        pass  # Use async version in daemon

    def set_hw_brightness(self, display_id: int, value: int) -> None:
        pass  # Use async version in daemon

    def set_dpms(self, state: str) -> None:
        pass  # Use async version in daemon

    def query_idle_ms(self) -> int | None:
        return None  # Use async version in daemon

    def query_dpms_state(self) -> str | None:
        return None  # Use async version in daemon
