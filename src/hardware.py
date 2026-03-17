"""HardwareBackend protocol + SubprocessBackend + MockHardwareBackend."""

from __future__ import annotations

import asyncio
from typing import Protocol


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


class MockHardwareBackend:
    """Records call log for test assertions."""

    def __init__(self, num_displays: int = 3):
        self._num_displays = num_displays
        self.calls: list[tuple] = []

    def detect_displays(self) -> list[dict]:
        return [{"id": i + 1} for i in range(self._num_displays)]

    def apply_color_temp(self, temp: int, brightness: float, method: str) -> None:
        self.calls.append(("apply_color_temp", temp, brightness, method))

    def reset_color_temp(self, method: str) -> None:
        self.calls.append(("reset_color_temp", method))

    def set_hw_brightness(self, display_id: int, value: int) -> None:
        self.calls.append(("set_hw_brightness", display_id, value))

    def clear(self) -> None:
        self.calls.clear()


class SubprocessBackend:
    """Real implementation using asyncio subprocess calls."""

    def __init__(self):
        self._displays: list[dict] | None = None

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

    async def async_set_hw_brightness(self, display_id: int, value: int) -> None:
        cmd = self._build_hw_brightness_cmd(display_id, value)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

    async def async_detect_displays(self) -> list[dict]:
        proc = await asyncio.create_subprocess_exec(
            "ddcutil", "detect",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await proc.communicate()
        displays = []
        for line in stdout.decode().splitlines():
            if line.startswith("Display "):
                try:
                    display_id = int(line.split()[1])
                    displays.append({"id": display_id})
                except (IndexError, ValueError):
                    pass
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
