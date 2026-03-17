"""Tests for brightness.py — SW/HW brightness state machine."""

import pytest

from brightness import bright_up, bright_down, BrightnessState, Action


@pytest.fixture
def default_state():
    """Default state: SW at max (100), HW at 0."""
    return BrightnessState(sw_brightness=100, hw_brightness=0)


@pytest.fixture
def cfg():
    return {"hw_step": 5, "sw_step": 5, "sw_min": 10}


class TestBrightUp:
    """Bright-up: SW first (back to 1.0), then HW."""

    def test_sw_below_max_increases_sw(self, cfg):
        state = BrightnessState(sw_brightness=50, hw_brightness=0)
        new_state, action = bright_up(state, cfg)
        assert new_state.sw_brightness == 55
        assert new_state.hw_brightness == 0
        assert action == Action.APPLY_SW

    def test_sw_at_max_increases_hw(self, cfg):
        state = BrightnessState(sw_brightness=100, hw_brightness=50)
        new_state, action = bright_up(state, cfg)
        assert new_state.sw_brightness == 100
        assert new_state.hw_brightness == 55
        assert action == Action.APPLY_HW

    def test_sw_reaches_max_then_hw(self, cfg):
        state = BrightnessState(sw_brightness=95, hw_brightness=0)
        new_state, action = bright_up(state, cfg)
        assert new_state.sw_brightness == 100
        assert action == Action.APPLY_SW

    def test_both_at_max_no_change(self, cfg):
        state = BrightnessState(sw_brightness=100, hw_brightness=100)
        new_state, action = bright_up(state, cfg)
        assert new_state.sw_brightness == 100
        assert new_state.hw_brightness == 100
        assert action == Action.NONE

    def test_hw_clamps_at_100(self, cfg):
        state = BrightnessState(sw_brightness=100, hw_brightness=98)
        new_state, action = bright_up(state, cfg)
        assert new_state.hw_brightness == 100

    def test_custom_step(self, cfg):
        cfg["sw_step"] = 10
        state = BrightnessState(sw_brightness=80, hw_brightness=0)
        new_state, action = bright_up(state, cfg)
        assert new_state.sw_brightness == 90


class TestBrightDown:
    """Bright-down: HW first (to 0%), then SW."""

    def test_hw_above_zero_decreases_hw(self, cfg):
        state = BrightnessState(sw_brightness=100, hw_brightness=50)
        new_state, action = bright_down(state, cfg)
        assert new_state.hw_brightness == 45
        assert new_state.sw_brightness == 100
        assert action == Action.APPLY_HW

    def test_hw_at_zero_decreases_sw(self, cfg):
        state = BrightnessState(sw_brightness=80, hw_brightness=0)
        new_state, action = bright_down(state, cfg)
        assert new_state.sw_brightness == 75
        assert new_state.hw_brightness == 0
        assert action == Action.APPLY_SW

    def test_hw_reaches_zero_then_sw(self, cfg):
        state = BrightnessState(sw_brightness=100, hw_brightness=5)
        new_state, action = bright_down(state, cfg)
        assert new_state.hw_brightness == 0
        assert action == Action.APPLY_HW

    def test_sw_clamps_at_min(self, cfg):
        state = BrightnessState(sw_brightness=10, hw_brightness=0)
        new_state, action = bright_down(state, cfg)
        assert new_state.sw_brightness == 10
        assert action == Action.NONE

    def test_sw_doesnt_go_below_min(self, cfg):
        state = BrightnessState(sw_brightness=12, hw_brightness=0)
        new_state, action = bright_down(state, cfg)
        assert new_state.sw_brightness == 10

    def test_hw_clamps_at_zero(self, cfg):
        state = BrightnessState(sw_brightness=100, hw_brightness=3)
        new_state, action = bright_down(state, cfg)
        assert new_state.hw_brightness == 0

    def test_both_at_min_no_change(self, cfg):
        state = BrightnessState(sw_brightness=10, hw_brightness=0)
        new_state, action = bright_down(state, cfg)
        assert action == Action.NONE


class TestBrightnessState:
    """BrightnessState is a simple data class."""

    def test_creation(self):
        s = BrightnessState(sw_brightness=75, hw_brightness=50)
        assert s.sw_brightness == 75
        assert s.hw_brightness == 50

    def test_equality(self):
        a = BrightnessState(sw_brightness=75, hw_brightness=50)
        b = BrightnessState(sw_brightness=75, hw_brightness=50)
        assert a == b
