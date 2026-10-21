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


class TestSubprocessBackendResults:
    """SubprocessBackend reports ddcutil failures instead of discarding them."""

    @pytest.mark.asyncio
    async def test_run_cmd_returns_exit_code_and_stderr(self):
        from hardware import SubprocessBackend
        rc, err = await SubprocessBackend()._run_cmd(["sh", "-c", "echo oops >&2; exit 3"])
        assert rc == 3
        assert err == "oops"

    @pytest.mark.asyncio
    async def test_run_cmd_success(self):
        from hardware import SubprocessBackend
        assert await SubprocessBackend()._run_cmd(["true"]) == (0, "")

    @pytest.mark.asyncio
    async def test_parallel_set_returns_only_failures(self, monkeypatch):
        from hardware import SubprocessBackend
        backend = SubprocessBackend()

        async def fake_run(cmd):
            if cmd[1:3] == ["--bus", "4"]:
                return 1, "Bus /dev/i2c-4 not found"
            return 0, ""

        monkeypatch.setattr(backend, "_run_cmd", fake_run)
        displays = [{"id": 1, "bus": 3}, {"id": 2, "bus": 4}, {"id": 3, "bus": 5}]
        failures = await backend.async_set_hw_brightness_parallel(displays, 40)
        assert failures == [({"id": 2, "bus": 4}, "Bus /dev/i2c-4 not found")]

    @pytest.mark.asyncio
    async def test_parallel_set_all_ok_returns_empty(self, monkeypatch):
        from hardware import SubprocessBackend
        backend = SubprocessBackend()

        async def fake_run(cmd):
            return 0, ""

        monkeypatch.setattr(backend, "_run_cmd", fake_run)
        displays = [{"id": 1, "bus": 3}, {"id": 2, "bus": 4}]
        assert await backend.async_set_hw_brightness_parallel(displays, 40) == []

    @pytest.mark.asyncio
    async def test_detect_records_stderr(self, monkeypatch):
        import hardware
        from hardware import SubprocessBackend

        class FakeProc:
            async def communicate(self):
                return b"", b"Permission denied: /dev/i2c-3\n"

        async def fake_exec(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(hardware.asyncio, "create_subprocess_exec", fake_exec)
        backend = SubprocessBackend()
        assert await backend.async_detect_displays() == []
        assert backend.last_detect_error == "Permission denied: /dev/i2c-3"

    @pytest.mark.asyncio
    async def test_run_cmd_captures_errors_printed_to_stdout(self):
        # ddcutil 1.4 prints e.g. "Bus /dev/i2c-99 not found" on stdout, not stderr.
        from hardware import SubprocessBackend
        rc, out = await SubprocessBackend()._run_cmd(
            ["sh", "-c", "echo 'Bus /dev/i2c-99 not found'; exit 1"]
        )
        assert rc == 1
        assert out == "Bus /dev/i2c-99 not found"

    @pytest.mark.asyncio
    async def test_detect_falls_back_to_stdout_when_no_displays(self, monkeypatch):
        import hardware
        from hardware import SubprocessBackend

        class FakeProc:
            async def communicate(self):
                return b"No displays found.\n", b""

        async def fake_exec(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(hardware.asyncio, "create_subprocess_exec", fake_exec)
        backend = SubprocessBackend()
        assert await backend.async_detect_displays() == []
        assert backend.last_detect_error == "No displays found."

    @pytest.mark.asyncio
    async def test_detect_error_empty_when_displays_found(self, monkeypatch):
        import hardware
        from hardware import SubprocessBackend

        class FakeProc:
            async def communicate(self):
                return b"Display 1\n   I2C bus:  /dev/i2c-3\n", b""

        async def fake_exec(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(hardware.asyncio, "create_subprocess_exec", fake_exec)
        backend = SubprocessBackend()
        assert await backend.async_detect_displays() == [{"id": 1, "bus": 3}]
        assert backend.last_detect_error == ""


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


class TestParseDpmsState:
    """`xset q` → the DPMS power level, or None."""

    def test_on(self):
        from hardware import parse_dpms_state
        out = "DPMS (Display Power Management Signaling):\n  Standby: 0    Suspend: 0    Off: 0\n  DPMS is Enabled\n  Monitor is On\n"
        assert parse_dpms_state(out) == "On"

    def test_standby_suspend_off(self):
        from hardware import parse_dpms_state
        assert parse_dpms_state("  Monitor is in Standby\n") == "Standby"
        assert parse_dpms_state("  Monitor is in Suspend\n") == "Suspend"
        assert parse_dpms_state("  Monitor is Off\n") == "Off"

    def test_dpms_disabled_has_no_state(self):
        from hardware import parse_dpms_state
        assert parse_dpms_state("  DPMS is Disabled\n") is None
        assert parse_dpms_state("") is None


class TestParseIdleMs:
    def test_parses_xprintidle_output(self):
        from hardware import parse_idle_ms
        assert parse_idle_ms("61234\n") == 61234

    def test_garbage_is_none(self):
        from hardware import parse_idle_ms
        assert parse_idle_ms("") is None
        assert parse_idle_ms("couldn't open display") is None
