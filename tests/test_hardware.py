"""Tests for hardware.py — MockHardwareBackend verifies call sequences."""

import pytest

from hardware import MockHardwareBackend


class TestMockBackend:
    """MockHardwareBackend records calls for test assertions."""

    def test_records_apply_color_temp(self):
        backend = MockHardwareBackend()
        backend.apply_color_temp(2800, 1.0, "randr")
        assert len(backend.calls) == 1
        assert backend.calls[0] == ("apply_color_temp", 2800, 1.0, "randr")

    def test_records_reset_color_temp(self):
        backend = MockHardwareBackend()
        backend.reset_color_temp("randr")
        assert backend.calls[0] == ("reset_color_temp", "randr")

    def test_records_set_hw_brightness(self):
        backend = MockHardwareBackend()
        backend.set_hw_brightness(1, 50)
        assert backend.calls[0] == ("set_hw_brightness", 1, 50)

    def test_detect_displays_returns_configured(self):
        backend = MockHardwareBackend(num_displays=3)
        displays = backend.detect_displays()
        assert len(displays) == 3
        assert displays[0]["id"] == 1
        assert displays[0]["bus"] == 3
        assert displays[2]["id"] == 3
        assert displays[2]["bus"] == 5

    def test_call_sequence_preserved(self):
        backend = MockHardwareBackend(num_displays=3)
        backend.apply_color_temp(2800, 0.80, "randr")
        backend.set_hw_brightness(1, 50)
        backend.set_hw_brightness(2, 50)
        backend.set_hw_brightness(3, 50)
        assert len(backend.calls) == 4
        assert backend.calls[0][0] == "apply_color_temp"
        assert backend.calls[1] == ("set_hw_brightness", 1, 50)
        assert backend.calls[2] == ("set_hw_brightness", 2, 50)
        assert backend.calls[3] == ("set_hw_brightness", 3, 50)

    def test_clear_calls(self):
        backend = MockHardwareBackend()
        backend.apply_color_temp(2800, 1.0, "randr")
        backend.clear()
        assert len(backend.calls) == 0


class TestSubprocessBackendCommands:
    """Verify SubprocessBackend builds correct command lines."""

    def test_apply_color_temp_cmd(self):
        from hardware import SubprocessBackend
        cmd = SubprocessBackend._build_apply_cmd(2800, 0.85, "randr")
        assert cmd == ["gammastep", "-m", "randr", "-P", "-O", "2800", "-b", "0.85:0.85"]

    def test_reset_color_temp_cmd(self):
        from hardware import SubprocessBackend
        cmd = SubprocessBackend._build_reset_cmd("randr")
        assert cmd == ["gammastep", "-m", "randr", "-P", "-x"]

    def test_set_hw_brightness_cmd(self):
        from hardware import SubprocessBackend
        cmd = SubprocessBackend._build_hw_brightness_cmd(2, 75)
        assert cmd == ["ddcutil", "-d", "2", "setvcp", "10", "75"]

    def test_brightness_decimal_formatting(self):
        from hardware import SubprocessBackend
        # sw_brightness=10 → 0.10, sw_brightness=100 → 1.00
        cmd = SubprocessBackend._build_apply_cmd(2800, 0.10, "randr")
        assert "0.10:0.10" in cmd[-1] or "0.1:0.1" in cmd[-1]


class TestDpms:
    """DPMS power control — via `xset dpms force <state>`."""

    def test_mock_set_dpms_records_call(self):
        backend = MockHardwareBackend()
        backend.set_dpms("standby")
        assert backend.calls[0] == ("set_dpms", "standby")

    def test_mock_set_dpms_multiple_states(self):
        backend = MockHardwareBackend()
        backend.set_dpms("standby")
        backend.set_dpms("on")
        assert backend.calls == [("set_dpms", "standby"), ("set_dpms", "on")]

    def test_subprocess_backend_builds_dpms_cmd_standby(self):
        from hardware import SubprocessBackend
        cmd = SubprocessBackend._build_dpms_cmd("standby")
        assert cmd == ["xset", "dpms", "force", "standby"]

    def test_subprocess_backend_builds_dpms_cmd_on(self):
        from hardware import SubprocessBackend
        cmd = SubprocessBackend._build_dpms_cmd("on")
        assert cmd == ["xset", "dpms", "force", "on"]
