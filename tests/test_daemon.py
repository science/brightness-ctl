"""Tests for daemon.py — command dispatch, state management, apply logic."""

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

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
