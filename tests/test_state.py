"""Tests for state.py — JSON state with atomic writes."""

import json
from pathlib import Path

import pytest

from state import AppState, load_state, save_state


class TestDefaultState:
    """Default state values when no file exists."""

    def test_load_missing_file(self, tmp_path):
        state = load_state(tmp_path / "state.json")
        assert state.enabled is True
        assert state.offset == 0
        assert state.sw_brightness == 100
        assert state.hw_brightness == 0

    def test_default_state_object(self):
        state = AppState()
        assert state.enabled is True
        assert state.offset == 0
        assert state.sw_brightness == 100
        assert state.hw_brightness == 0


class TestRoundTrip:
    """Save then load preserves all fields."""

    def test_round_trip_defaults(self, tmp_path):
        path = tmp_path / "state.json"
        state = AppState()
        save_state(state, path)
        loaded = load_state(path)
        assert loaded == state

    def test_round_trip_custom_values(self, tmp_path):
        path = tmp_path / "state.json"
        state = AppState(enabled=False, offset=-400, sw_brightness=60, hw_brightness=75)
        save_state(state, path)
        loaded = load_state(path)
        assert loaded.enabled is False
        assert loaded.offset == -400
        assert loaded.sw_brightness == 60
        assert loaded.hw_brightness == 75

    def test_round_trip_disabled(self, tmp_path):
        path = tmp_path / "state.json"
        state = AppState(enabled=False)
        save_state(state, path)
        loaded = load_state(path)
        assert loaded.enabled is False


class TestAtomicWrite:
    """save_state uses atomic write (tmp + rename)."""

    def test_no_tmp_file_left(self, tmp_path):
        path = tmp_path / "state.json"
        save_state(AppState(), path)
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "state.json"

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "subdir" / "state.json"
        save_state(AppState(), path)
        assert path.exists()

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "state.json"
        save_state(AppState(offset=100), path)
        save_state(AppState(offset=200), path)
        loaded = load_state(path)
        assert loaded.offset == 200


class TestJsonFormat:
    """State file is valid JSON with expected keys."""

    def test_file_is_valid_json(self, tmp_path):
        path = tmp_path / "state.json"
        save_state(AppState(offset=-200, sw_brightness=80), path)
        data = json.loads(path.read_text())
        assert data["enabled"] is True
        assert data["offset"] == -200
        assert data["sw_brightness"] == 80
        assert data["hw_brightness"] == 0

    def test_load_handles_extra_keys(self, tmp_path):
        """Future-proofing: extra keys in JSON don't break loading."""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({
            "enabled": True, "offset": 0,
            "sw_brightness": 100, "hw_brightness": 0,
            "future_field": "hello"
        }))
        state = load_state(path)
        assert state.enabled is True

    def test_load_handles_missing_keys(self, tmp_path):
        """Partial JSON gets defaults for missing fields."""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"enabled": False, "offset": -100}))
        state = load_state(path)
        assert state.enabled is False
        assert state.offset == -100
        assert state.sw_brightness == 100
        assert state.hw_brightness == 0


class TestAutobrightnessFields:
    """New autobrightness state fields."""

    def test_defaults(self):
        state = AppState()
        assert state.autobrightness_enabled is False
        assert state.anchor_combined is None
        assert state.cal_min is None
        assert state.cal_max is None

    def test_round_trip(self, tmp_path):
        path = tmp_path / "state.json"
        state = AppState(
            autobrightness_enabled=True,
            anchor_combined=120.5,
            cal_min=30.0,
            cal_max=180.0,
        )
        save_state(state, path)
        loaded = load_state(path)
        assert loaded.autobrightness_enabled is True
        assert loaded.anchor_combined == 120.5
        assert loaded.cal_min == 30.0
        assert loaded.cal_max == 180.0

    def test_backward_compat_loading(self, tmp_path):
        """Old state file without autobrightness fields loads with defaults."""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({
            "enabled": True, "offset": 0,
            "sw_brightness": 100, "hw_brightness": 0,
        }))
        state = load_state(path)
        assert state.autobrightness_enabled is False
        assert state.anchor_combined is None
        assert state.cal_min is None
        assert state.cal_max is None

    def test_null_values_load_as_none(self, tmp_path):
        """Explicit null values in JSON load as None."""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({
            "enabled": True, "offset": 0,
            "sw_brightness": 100, "hw_brightness": 0,
            "autobrightness_enabled": False,
            "anchor_combined": None,
            "cal_min": None, "cal_max": None,
        }))
        state = load_state(path)
        assert state.anchor_combined is None
        assert state.cal_min is None
