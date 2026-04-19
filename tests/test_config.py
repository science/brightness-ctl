"""Tests for config.py — TOML loading with defaults, validation, bash config migration."""

import os
import textwrap
from pathlib import Path

import pytest

from config import load_config, DEFAULT_CONFIG, migrate_bash_config


class TestDefaults:
    """Config defaults are sane without any config file."""

    def test_returns_defaults_when_no_file(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.toml")
        assert cfg == DEFAULT_CONFIG

    def test_default_temps(self):
        assert DEFAULT_CONFIG["day_temp"] == 2800
        assert DEFAULT_CONFIG["night_temp"] == 2200

    def test_default_step(self):
        assert DEFAULT_CONFIG["step"] == 200

    def test_default_temp_range(self):
        assert DEFAULT_CONFIG["min_temp"] == 1500
        assert DEFAULT_CONFIG["max_temp"] == 6500

    def test_default_transition_hours(self):
        assert DEFAULT_CONFIG["dawn_start"] == 6
        assert DEFAULT_CONFIG["dawn_end"] == 8
        assert DEFAULT_CONFIG["dusk_start"] == 18
        assert DEFAULT_CONFIG["dusk_end"] == 20

    def test_default_method(self):
        assert DEFAULT_CONFIG["method"] == "randr"

    def test_default_brightness(self):
        assert DEFAULT_CONFIG["hw_step"] == 5
        assert DEFAULT_CONFIG["sw_step"] == 5
        assert DEFAULT_CONFIG["sw_min"] == 10

    def test_default_screensaver(self):
        assert DEFAULT_CONFIG["screensaver_monitor_off"] is True
        assert DEFAULT_CONFIG["screensaver_dpms_mode"] == "standby"

    def test_default_autobrightness(self):
        assert DEFAULT_CONFIG["autobrightness_range"] == 40
        assert DEFAULT_CONFIG["autobrightness_interval"] == 60
        assert DEFAULT_CONFIG["luminance_log_interval"] == 1800
        # camera_device defaults to None (auto-probe by VID:PID).
        # Hard-coding a node path here was a safety bug — see HOST_RESULTS.md
        # and docstring in camera.py.
        assert DEFAULT_CONFIG["camera_device"] is None
        assert DEFAULT_CONFIG["camera_frames"] == 4
        assert DEFAULT_CONFIG["calibration_lookback_days"] == 7
        assert DEFAULT_CONFIG["calibration_percentile_lo"] == 5
        assert DEFAULT_CONFIG["calibration_percentile_hi"] == 95
        assert DEFAULT_CONFIG["log_retention_days"] == 90


class TestTomlLoading:
    """Load TOML config and merge with defaults."""

    def test_loads_toml_file(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(textwrap.dedent("""\
            day_temp = 3200
            night_temp = 2500
        """))
        cfg = load_config(config_file)
        assert cfg["day_temp"] == 3200
        assert cfg["night_temp"] == 2500

    def test_missing_keys_get_defaults(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("day_temp = 3000\n")
        cfg = load_config(config_file)
        assert cfg["day_temp"] == 3000
        assert cfg["night_temp"] == DEFAULT_CONFIG["night_temp"]
        assert cfg["step"] == DEFAULT_CONFIG["step"]

    def test_all_keys_overridden(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(textwrap.dedent("""\
            day_temp = 4000
            night_temp = 3000
            step = 100
            min_temp = 2000
            max_temp = 5000
            dawn_start = 7
            dawn_end = 9
            dusk_start = 17
            dusk_end = 19
            method = "wayland"
            hw_step = 10
            sw_step = 10
            sw_min = 20
        """))
        cfg = load_config(config_file)
        assert cfg["day_temp"] == 4000
        assert cfg["night_temp"] == 3000
        assert cfg["step"] == 100
        assert cfg["min_temp"] == 2000
        assert cfg["max_temp"] == 5000
        assert cfg["dawn_start"] == 7
        assert cfg["dawn_end"] == 9
        assert cfg["dusk_start"] == 17
        assert cfg["dusk_end"] == 19
        assert cfg["method"] == "wayland"
        assert cfg["hw_step"] == 10
        assert cfg["sw_step"] == 10
        assert cfg["sw_min"] == 20

    def test_autobrightness_config_from_toml(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(textwrap.dedent("""\
            autobrightness_range = 50
            camera_device = "/dev/video4"
            calibration_lookback_days = 14
        """))
        cfg = load_config(config_file)
        assert cfg["autobrightness_range"] == 50
        assert cfg["camera_device"] == "/dev/video4"
        assert cfg["calibration_lookback_days"] == 14
        # Unset keys get defaults
        assert cfg["autobrightness_interval"] == 60

    def test_unknown_keys_preserved(self, tmp_path):
        """Future-proofing: unknown keys don't cause errors."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("day_temp = 3000\nfuture_key = 42\n")
        cfg = load_config(config_file)
        assert cfg["day_temp"] == 3000
        assert cfg["future_key"] == 42


class TestBashMigration:
    """Migrate old bash-style config to TOML."""

    def test_migrates_bash_config(self, tmp_path):
        bash_config = tmp_path / "config"
        bash_config.write_text(textwrap.dedent("""\
            # redshift-ctl configuration
            DAY_TEMP=2800
            NIGHT_TEMP=2200
            STEP=200
            MIN_TEMP=1500
            MAX_TEMP=6500
            DAWN_START=6
            DAWN_END=8
            DUSK_START=18
            DUSK_END=20
            METHOD="randr"
            HW_STEP=5
            SW_STEP=5
            SW_MIN=10
        """))
        toml_path = tmp_path / "config.toml"
        result = migrate_bash_config(bash_config, toml_path)
        assert result is True
        assert toml_path.exists()

        cfg = load_config(toml_path)
        assert cfg["day_temp"] == 2800
        assert cfg["night_temp"] == 2200
        assert cfg["step"] == 200
        assert cfg["method"] == "randr"
        assert cfg["hw_step"] == 5

    def test_skips_if_toml_exists(self, tmp_path):
        bash_config = tmp_path / "config"
        bash_config.write_text("DAY_TEMP=2800\n")
        toml_path = tmp_path / "config.toml"
        toml_path.write_text("day_temp = 3200\n")
        result = migrate_bash_config(bash_config, toml_path)
        assert result is False
        # Original TOML unchanged
        cfg = load_config(toml_path)
        assert cfg["day_temp"] == 3200

    def test_skips_if_no_bash_config(self, tmp_path):
        result = migrate_bash_config(
            tmp_path / "nonexistent", tmp_path / "config.toml"
        )
        assert result is False

    def test_handles_quoted_values(self, tmp_path):
        bash_config = tmp_path / "config"
        bash_config.write_text('METHOD="randr"\nDAY_TEMP=2800\n')
        toml_path = tmp_path / "config.toml"
        migrate_bash_config(bash_config, toml_path)
        cfg = load_config(toml_path)
        assert cfg["method"] == "randr"

    def test_skips_comments_and_blanks(self, tmp_path):
        bash_config = tmp_path / "config"
        bash_config.write_text(textwrap.dedent("""\
            # This is a comment

            DAY_TEMP=3000
            # Another comment
            NIGHT_TEMP=2500
        """))
        toml_path = tmp_path / "config.toml"
        migrate_bash_config(bash_config, toml_path)
        cfg = load_config(toml_path)
        assert cfg["day_temp"] == 3000
        assert cfg["night_temp"] == 2500

    def test_strips_inline_comments(self, tmp_path):
        bash_config = tmp_path / "config"
        bash_config.write_text(textwrap.dedent("""\
            DAY_TEMP=2800       # Daytime color temperature
            HW_STEP=5           # Hardware brightness step
            METHOD="randr"
        """))
        toml_path = tmp_path / "config.toml"
        migrate_bash_config(bash_config, toml_path)
        cfg = load_config(toml_path)
        assert cfg["day_temp"] == 2800
        assert cfg["hw_step"] == 5
