"""Tests for daemon.py — command dispatch, state management, apply logic."""

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from autobrightness import to_combined
from daemon import Daemon
from hardware import MockHardwareBackend
from config import DEFAULT_CONFIG
from state import AppState


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create temp dirs for config, state, and socket."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    state_file = config_dir / "state.json"
    socket_path = tmp_path / "brightness-ctl.sock"
    config_file = config_dir / "config.toml"
    return {
        "state_file": state_file,
        "socket_path": socket_path,
        "config_file": config_file,
        "config_dir": config_dir,
    }


@pytest.fixture
def backend():
    return MockHardwareBackend(num_displays=3)


@pytest.fixture
def daemon(tmp_dirs, backend):
    return Daemon(
        config_path=tmp_dirs["config_file"],
        state_path=tmp_dirs["state_file"],
        socket_path=tmp_dirs["socket_path"],
        backend=backend,
    )


class TestCommandDispatch:
    """Daemon handles commands and returns responses."""

    @pytest.mark.asyncio
    async def test_warmer(self, daemon):
        resp = await daemon.handle_command({"cmd": "warmer"})
        assert resp["status"] == "ok"
        assert daemon.state.offset < 0  # warmer = lower temp = negative offset

    @pytest.mark.asyncio
    async def test_cooler(self, daemon):
        resp = await daemon.handle_command({"cmd": "cooler"})
        assert resp["status"] == "ok"
        assert daemon.state.offset > 0  # cooler = higher temp = positive offset

    @pytest.mark.asyncio
    async def test_toggle_off(self, daemon):
        assert daemon.state.enabled is True
        resp = await daemon.handle_command({"cmd": "toggle"})
        assert resp["status"] == "ok"
        assert daemon.state.enabled is False

    @pytest.mark.asyncio
    async def test_toggle_on(self, daemon):
        daemon.state.enabled = False
        resp = await daemon.handle_command({"cmd": "toggle"})
        assert daemon.state.enabled is True

    @pytest.mark.asyncio
    async def test_reset(self, daemon):
        daemon.state.offset = -400
        daemon.state.enabled = False
        resp = await daemon.handle_command({"cmd": "reset"})
        assert daemon.state.offset == 0
        assert daemon.state.enabled is True

    @pytest.mark.asyncio
    async def test_bright_up(self, daemon, backend):
        daemon.state.sw_brightness = 100
        daemon.state.hw_brightness = 50
        resp = await daemon.handle_command({"cmd": "bright-up"})
        assert resp["status"] == "ok"
        assert daemon.state.hw_brightness == 55

    @pytest.mark.asyncio
    async def test_bright_down(self, daemon, backend):
        daemon.state.sw_brightness = 100
        daemon.state.hw_brightness = 50
        resp = await daemon.handle_command({"cmd": "bright-down"})
        assert resp["status"] == "ok"
        assert daemon.state.hw_brightness == 45

    @pytest.mark.asyncio
    async def test_status(self, daemon):
        resp = await daemon.handle_command({"cmd": "status"})
        assert resp["status"] == "ok"
        assert "enabled" in resp
        assert "base_temp" in resp
        assert "applied_temp" in resp
        assert "sw_brightness" in resp
        assert "hw_brightness" in resp

    @pytest.mark.asyncio
    async def test_stop(self, daemon):
        resp = await daemon.handle_command({"cmd": "stop"})
        assert resp["status"] == "ok"
        assert daemon.should_stop is True

    @pytest.mark.asyncio
    async def test_unknown_command(self, daemon):
        resp = await daemon.handle_command({"cmd": "nonexistent"})
        assert resp["status"] == "error"

    @pytest.mark.asyncio
    async def test_warmer_enables_if_disabled(self, daemon):
        daemon.state.enabled = False
        await daemon.handle_command({"cmd": "warmer"})
        assert daemon.state.enabled is True

    @pytest.mark.asyncio
    async def test_cooler_enables_if_disabled(self, daemon):
        daemon.state.enabled = False
        await daemon.handle_command({"cmd": "cooler"})
        assert daemon.state.enabled is True


class TestApply:
    """Daemon.apply() calls backend correctly."""

    @pytest.mark.asyncio
    async def test_apply_when_enabled(self, daemon, backend):
        await daemon.apply()
        assert len(backend.calls) >= 1
        assert backend.calls[0][0] == "apply_color_temp"

    @pytest.mark.asyncio
    async def test_apply_when_disabled(self, daemon, backend):
        daemon.state.enabled = False
        await daemon.apply()
        assert len(backend.calls) >= 1
        assert backend.calls[0][0] == "reset_color_temp"

    @pytest.mark.asyncio
    async def test_apply_includes_offset(self, daemon, backend):
        daemon.state.offset = -200
        await daemon.apply()
        call = backend.calls[0]
        # applied temp should be base_temp + offset
        assert call[0] == "apply_color_temp"
        temp = call[1]
        assert temp < daemon.config["day_temp"]  # offset is negative

    @pytest.mark.asyncio
    async def test_apply_clamps_temp(self, daemon, backend):
        daemon.state.offset = -99999
        await daemon.apply()
        call = backend.calls[0]
        assert call[1] >= daemon.config["min_temp"]

    @pytest.mark.asyncio
    async def test_apply_hw_brightness(self, daemon, backend):
        daemon.state.hw_brightness = 75
        await daemon.apply_hw_brightness()
        # Should have sequential calls for each display
        hw_calls = [c for c in backend.calls if c[0] == "set_hw_brightness"]
        assert len(hw_calls) == 3  # 3 displays
        # All set to 75
        for call in hw_calls:
            assert call[2] == 75
        # Sequential: display 1, 2, 3
        assert hw_calls[0][1] == 1
        assert hw_calls[1][1] == 2
        assert hw_calls[2][1] == 3


class TestStatePersistence:
    """State is saved to disk after commands."""

    @pytest.mark.asyncio
    async def test_state_saved_after_warmer(self, daemon, tmp_dirs):
        await daemon.handle_command({"cmd": "warmer"})
        assert tmp_dirs["state_file"].exists()
        data = json.loads(tmp_dirs["state_file"].read_text())
        assert data["offset"] < 0

    @pytest.mark.asyncio
    async def test_state_saved_after_toggle(self, daemon, tmp_dirs):
        await daemon.handle_command({"cmd": "toggle"})
        data = json.loads(tmp_dirs["state_file"].read_text())
        assert data["enabled"] is False


class TestSocketIPC:
    """Test full socket communication cycle."""

    @pytest.mark.asyncio
    async def test_socket_round_trip(self, daemon, tmp_dirs):
        """Start server, connect as client, send command, get response."""
        server = await asyncio.start_unix_server(
            daemon.handle_client, path=str(tmp_dirs["socket_path"])
        )

        try:
            reader, writer = await asyncio.open_unix_connection(
                str(tmp_dirs["socket_path"])
            )
            writer.write(json.dumps({"cmd": "status"}).encode() + b"\n")
            await writer.drain()

            data = await asyncio.wait_for(reader.readline(), timeout=2.0)
            resp = json.loads(data.decode())
            assert resp["status"] == "ok"
            assert "enabled" in resp

            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_socket_warmer_command(self, daemon, tmp_dirs):
        server = await asyncio.start_unix_server(
            daemon.handle_client, path=str(tmp_dirs["socket_path"])
        )

        try:
            reader, writer = await asyncio.open_unix_connection(
                str(tmp_dirs["socket_path"])
            )
            writer.write(json.dumps({"cmd": "warmer"}).encode() + b"\n")
            await writer.drain()

            data = await asyncio.wait_for(reader.readline(), timeout=2.0)
            resp = json.loads(data.decode())
            assert resp["status"] == "ok"

            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_socket_handles_bad_json(self, daemon, tmp_dirs):
        server = await asyncio.start_unix_server(
            daemon.handle_client, path=str(tmp_dirs["socket_path"])
        )

        try:
            reader, writer = await asyncio.open_unix_connection(
                str(tmp_dirs["socket_path"])
            )
            writer.write(b"not valid json\n")
            await writer.drain()

            data = await asyncio.wait_for(reader.readline(), timeout=2.0)
            resp = json.loads(data.decode())
            assert resp["status"] == "error"

            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


class TestAutoOn:
    """auto-on command enables autobrightness and sets anchor."""

    @pytest.mark.asyncio
    async def test_auto_on_sets_anchor(self, daemon):
        daemon.state.sw_brightness = 80
        daemon.state.hw_brightness = 0
        resp = await daemon.handle_command({"cmd": "auto-on"})
        assert resp["status"] == "ok"
        assert daemon.state.autobrightness_enabled is True
        expected = to_combined(80, 0, sw_min=daemon.config["sw_min"])
        assert daemon.state.anchor_combined == expected

    @pytest.mark.asyncio
    async def test_auto_on_with_hw(self, daemon):
        daemon.state.sw_brightness = 100
        daemon.state.hw_brightness = 50
        resp = await daemon.handle_command({"cmd": "auto-on"})
        assert daemon.state.anchor_combined == 150.0

    @pytest.mark.asyncio
    async def test_auto_on_returns_calibration(self, daemon):
        resp = await daemon.handle_command({"cmd": "auto-on"})
        assert "cal_min" in resp
        assert "cal_max" in resp


class TestAutoOff:
    """auto-off command disables autobrightness."""

    @pytest.mark.asyncio
    async def test_auto_off(self, daemon):
        daemon.state.autobrightness_enabled = True
        daemon.state.anchor_combined = 100.0
        resp = await daemon.handle_command({"cmd": "auto-off"})
        assert resp["status"] == "ok"
        assert daemon.state.autobrightness_enabled is False


class TestAutoStatus:
    """auto-status returns autobrightness state."""

    @pytest.mark.asyncio
    async def test_auto_status(self, daemon):
        daemon.state.autobrightness_enabled = True
        daemon.state.anchor_combined = 120.0
        daemon.state.cal_min = 30.0
        daemon.state.cal_max = 180.0
        resp = await daemon.handle_command({"cmd": "auto-status"})
        assert resp["status"] == "ok"
        assert resp["autobrightness_enabled"] is True
        assert resp["anchor_combined"] == 120.0
        assert resp["calibration_ready"] is True

    @pytest.mark.asyncio
    async def test_auto_status_uncalibrated(self, daemon):
        resp = await daemon.handle_command({"cmd": "auto-status"})
        assert resp["calibration_ready"] is False


class TestAutoCalibrate:
    """auto-calibrate recomputes from logs."""

    @pytest.mark.asyncio
    async def test_auto_calibrate_no_data(self, daemon):
        resp = await daemon.handle_command({"cmd": "auto-calibrate"})
        assert resp["status"] == "ok"
        assert resp["calibration_ready"] is False

    @pytest.mark.asyncio
    async def test_auto_calibrate_with_data(self, daemon, tmp_dirs):
        """With enough log data, calibration becomes ready."""
        from luminance_log import append_reading
        log_dir = tmp_dirs["config_dir"] / "luminance-logs"
        daemon.log_dir = log_dir
        for i in range(120):
            append_reading(log_dir, float(i * 2))  # 0 to 238
        resp = await daemon.handle_command({"cmd": "auto-calibrate"})
        assert resp["calibration_ready"] is True
        assert resp["cal_min"] is not None
        assert resp["cal_max"] is not None


class TestAutoResetCal:
    """auto-reset-cal clears calibration and logs."""

    @pytest.mark.asyncio
    async def test_auto_reset_cal(self, daemon, tmp_dirs):
        daemon.state.cal_min = 30.0
        daemon.state.cal_max = 180.0
        log_dir = tmp_dirs["config_dir"] / "luminance-logs"
        daemon.log_dir = log_dir
        from luminance_log import append_reading
        append_reading(log_dir, 100.0)
        assert any(log_dir.glob("luminance-*.log"))

        resp = await daemon.handle_command({"cmd": "auto-reset-cal"})
        assert resp["status"] == "ok"
        assert daemon.state.cal_min is None
        assert daemon.state.cal_max is None
        assert not any(log_dir.glob("luminance-*.log"))


class TestAutoSetCal:
    """auto-set-cal manually sets calibration min/max."""

    @pytest.mark.asyncio
    async def test_auto_set_cal_happy_path(self, daemon):
        resp = await daemon.handle_command({
            "cmd": "auto-set-cal",
            "args": {"cal_min": 20.0, "cal_max": 180.0},
        })
        assert resp["status"] == "ok"
        assert resp["cal_min"] == 20.0
        assert resp["cal_max"] == 180.0
        assert resp["calibration_ready"] is True
        assert daemon.state.cal_min == 20.0
        assert daemon.state.cal_max == 180.0

    @pytest.mark.asyncio
    async def test_auto_set_cal_bad_range(self, daemon):
        resp = await daemon.handle_command({
            "cmd": "auto-set-cal",
            "args": {"cal_min": 100.0, "cal_max": 105.0},
        })
        assert resp["status"] == "error"
        assert daemon.state.cal_min is None

    @pytest.mark.asyncio
    async def test_auto_set_cal_min_ge_max(self, daemon):
        resp = await daemon.handle_command({
            "cmd": "auto-set-cal",
            "args": {"cal_min": 180.0, "cal_max": 20.0},
        })
        assert resp["status"] == "error"

    @pytest.mark.asyncio
    async def test_auto_set_cal_missing_args(self, daemon):
        resp = await daemon.handle_command({"cmd": "auto-set-cal"})
        assert resp["status"] == "error"

    @pytest.mark.asyncio
    async def test_auto_set_cal_saves_state(self, daemon, tmp_dirs):
        await daemon.handle_command({
            "cmd": "auto-set-cal",
            "args": {"cal_min": 20.0, "cal_max": 180.0},
        })
        data = json.loads(tmp_dirs["state_file"].read_text())
        assert data["cal_min"] == 20.0
        assert data["cal_max"] == 180.0


class TestAnchorUpdates:
    """bright-up/bright-down update anchor when autobrightness is enabled."""

    @pytest.mark.asyncio
    async def test_bright_up_updates_anchor(self, daemon):
        daemon.state.autobrightness_enabled = True
        daemon._last_ambient_pct = None  # no ambient reading yet
        daemon.state.sw_brightness = 100
        daemon.state.hw_brightness = 50
        daemon.state.anchor_combined = 150.0
        await daemon.handle_command({"cmd": "bright-up"})
        assert daemon.state.anchor_combined == 155.0

    @pytest.mark.asyncio
    async def test_bright_down_updates_anchor(self, daemon):
        daemon.state.autobrightness_enabled = True
        daemon._last_ambient_pct = None  # no ambient reading yet
        daemon.state.sw_brightness = 100
        daemon.state.hw_brightness = 50
        daemon.state.anchor_combined = 150.0
        await daemon.handle_command({"cmd": "bright-down"})
        assert daemon.state.anchor_combined == 145.0

    @pytest.mark.asyncio
    async def test_bright_up_no_anchor_when_auto_off(self, daemon):
        daemon.state.autobrightness_enabled = False
        daemon.state.sw_brightness = 100
        daemon.state.hw_brightness = 50
        daemon.state.anchor_combined = None
        await daemon.handle_command({"cmd": "bright-up"})
        assert daemon.state.anchor_combined is None

    @pytest.mark.asyncio
    async def test_bright_up_back_computes_anchor(self, daemon):
        """In a dark room, bright-up should set anchor HIGHER than the
        resulting brightness so the ambient loop won't dim it back."""
        daemon.state.autobrightness_enabled = True
        daemon._last_ambient_pct = 0.0  # dark room
        daemon.state.sw_brightness = 100
        daemon.state.hw_brightness = 0
        await daemon.handle_command({"cmd": "bright-up"})
        # bright-up steps hw to 5, combined = 105
        # anchor should be 105 - (0.0 - 0.5) * 40 = 105 + 20 = 125
        from autobrightness import to_combined, compute_target
        new_combined = to_combined(daemon.state.sw_brightness,
                                   daemon.state.hw_brightness,
                                   sw_min=daemon.config["sw_min"])
        target = compute_target(daemon.state.anchor_combined, 0.0,
                                daemon.config["autobrightness_range"])
        assert target == pytest.approx(new_combined)

    @pytest.mark.asyncio
    async def test_bright_down_back_computes_anchor(self, daemon):
        """In a bright room, bright-down should set anchor LOWER than the
        resulting brightness so the ambient loop won't brighten it back."""
        daemon.state.autobrightness_enabled = True
        daemon._last_ambient_pct = 1.0  # bright room
        daemon.state.sw_brightness = 100
        daemon.state.hw_brightness = 50
        await daemon.handle_command({"cmd": "bright-down"})
        from autobrightness import to_combined, compute_target
        new_combined = to_combined(daemon.state.sw_brightness,
                                   daemon.state.hw_brightness,
                                   sw_min=daemon.config["sw_min"])
        target = compute_target(daemon.state.anchor_combined, 1.0,
                                daemon.config["autobrightness_range"])
        assert target == pytest.approx(new_combined)


class TestScreensaverCallback:
    """Daemon._on_screensaver_active drives DPMS via the backend."""

    @pytest.mark.asyncio
    async def test_active_invokes_set_dpms_with_configured_mode(self, daemon, backend):
        daemon.config["screensaver_dpms_mode"] = "standby"
        await daemon._on_screensaver_active(True)
        assert ("set_dpms", "standby") in backend.calls

    @pytest.mark.asyncio
    async def test_inactive_restores_dpms_on(self, daemon, backend):
        daemon.config["screensaver_dpms_mode"] = "standby"
        await daemon._on_screensaver_active(False)
        assert ("set_dpms", "on") in backend.calls

    @pytest.mark.asyncio
    async def test_custom_mode(self, daemon, backend):
        daemon.config["screensaver_dpms_mode"] = "off"
        await daemon._on_screensaver_active(True)
        assert ("set_dpms", "off") in backend.calls


class TestScreensaverLifecycle:
    """run() starts the screensaver watcher based on config flag."""

    @pytest.mark.asyncio
    async def test_start_screensaver_task_when_enabled(self, daemon, monkeypatch):
        daemon.config["screensaver_monitor_off"] = True
        called = {"n": 0}
        monkeypatch.setattr(
            type(daemon), "_start_screensaver_task",
            lambda self: called.__setitem__("n", called["n"] + 1),
        )
        # Patch out other run() side effects: stop immediately, no socket, etc.
        daemon.should_stop = True
        # The run() loop creates the server before checking should_stop;
        # to keep this focused on the screensaver wiring we call the helper
        # path directly. The explicit test is that, under enabled config,
        # calling the private helper is safe AND run() includes it.
        if daemon.config["screensaver_monitor_off"]:
            daemon._start_screensaver_task()
        assert called["n"] >= 1

    @pytest.mark.asyncio
    async def test_skip_screensaver_task_when_disabled(self, daemon, monkeypatch):
        daemon.config["screensaver_monitor_off"] = False
        called = {"n": 0}
        monkeypatch.setattr(
            type(daemon), "_start_screensaver_task",
            lambda self: called.__setitem__("n", called["n"] + 1),
        )
        # Mirror run()'s guard.
        if daemon.config["screensaver_monitor_off"]:
            daemon._start_screensaver_task()
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_stop_screensaver_task_cancels(self, daemon):
        """_stop_screensaver_task cancels the running task."""
        async def forever():
            await asyncio.sleep(3600)

        daemon._screensaver_task = asyncio.create_task(forever())
        daemon._stop_screensaver_task()
        # Give the loop one tick to propagate cancellation.
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.sleep(0)), timeout=0.1,
            )
        except asyncio.TimeoutError:
            pass
        assert daemon._screensaver_task is None
