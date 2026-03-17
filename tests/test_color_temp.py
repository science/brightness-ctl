"""Tests for color_temp.py — get_base_temp() pure function."""

import pytest

from config import DEFAULT_CONFIG
from color_temp import get_base_temp


@pytest.fixture
def cfg():
    return dict(DEFAULT_CONFIG)


class TestNighttime:
    """Before dawn and after dusk → NIGHT_TEMP."""

    def test_midnight(self, cfg):
        assert get_base_temp(0, 0, cfg) == 2200

    def test_3am(self, cfg):
        assert get_base_temp(3, 0, cfg) == 2200

    def test_just_before_dawn(self, cfg):
        assert get_base_temp(5, 59, cfg) == 2200

    def test_just_after_dusk(self, cfg):
        assert get_base_temp(20, 0, cfg) == 2200

    def test_11pm(self, cfg):
        assert get_base_temp(23, 0, cfg) == 2200


class TestDaytime:
    """Between dawn_end and dusk_start → DAY_TEMP."""

    def test_noon(self, cfg):
        assert get_base_temp(12, 0, cfg) == 2800

    def test_dawn_end(self, cfg):
        assert get_base_temp(8, 0, cfg) == 2800

    def test_just_before_dusk(self, cfg):
        assert get_base_temp(17, 59, cfg) == 2800

    def test_10am(self, cfg):
        assert get_base_temp(10, 0, cfg) == 2800


class TestDawnTransition:
    """Linear interpolation from NIGHT_TEMP to DAY_TEMP during dawn."""

    def test_dawn_start(self, cfg):
        # At dawn_start (6:00), should be NIGHT_TEMP
        assert get_base_temp(6, 0, cfg) == 2200

    def test_dawn_midpoint(self, cfg):
        # At 7:00, halfway through 6:00-8:00, midpoint of 2200-2800 = 2500
        assert get_base_temp(7, 0, cfg) == 2500

    def test_dawn_quarter(self, cfg):
        # At 6:30, quarter through → 2200 + 600*0.25 = 2350
        assert get_base_temp(6, 30, cfg) == 2350

    def test_dawn_three_quarter(self, cfg):
        # At 7:30, 75% through → 2200 + 600*0.75 = 2650
        assert get_base_temp(7, 30, cfg) == 2650


class TestDuskTransition:
    """Linear interpolation from DAY_TEMP to NIGHT_TEMP during dusk."""

    def test_dusk_start(self, cfg):
        # At dusk_start (18:00), should be DAY_TEMP
        assert get_base_temp(18, 0, cfg) == 2800

    def test_dusk_midpoint(self, cfg):
        # At 19:00, halfway through 18:00-20:00, midpoint = 2500
        assert get_base_temp(19, 0, cfg) == 2500

    def test_dusk_quarter(self, cfg):
        # At 18:30, 25% through → 2800 + (2200-2800)*0.25 = 2800 - 150 = 2650
        assert get_base_temp(18, 30, cfg) == 2650

    def test_dusk_three_quarter(self, cfg):
        # At 19:30, 75% through → 2800 + (2200-2800)*0.75 = 2800 - 450 = 2350
        assert get_base_temp(19, 30, cfg) == 2350


class TestCustomConfig:
    """Different config values change the output."""

    def test_custom_temps(self, cfg):
        cfg["day_temp"] = 5000
        cfg["night_temp"] = 3000
        assert get_base_temp(12, 0, cfg) == 5000
        assert get_base_temp(0, 0, cfg) == 3000

    def test_custom_dawn_window(self, cfg):
        cfg["dawn_start"] = 5
        cfg["dawn_end"] = 7
        # 6:00 is midpoint of 5:00-7:00
        assert get_base_temp(6, 0, cfg) == 2500

    def test_custom_dusk_window(self, cfg):
        cfg["dusk_start"] = 20
        cfg["dusk_end"] = 22
        # 21:00 is midpoint of 20:00-22:00
        assert get_base_temp(21, 0, cfg) == 2500


class TestEdgeCases:
    """Boundary conditions and edge cases."""

    def test_minute_59(self, cfg):
        # 7:59 is almost at dawn_end (8:00)
        # fractional = 7 + 59/60 = 7.9833...
        # progress = (7.9833 - 6) / (8 - 6) = 1.9833/2 = 0.9917
        # temp = 2200 + 600 * 0.9917 = 2795.0
        assert get_base_temp(7, 59, cfg) == 2795

    def test_minute_0(self, cfg):
        assert get_base_temp(6, 0, cfg) == 2200

    def test_returns_int(self, cfg):
        result = get_base_temp(7, 15, cfg)
        assert isinstance(result, int)
