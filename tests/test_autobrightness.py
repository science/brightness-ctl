"""Tests for autobrightness.py — combined scale, calibration, ambient adjustment."""

import pytest

from autobrightness import (
    to_combined,
    from_combined,
    calibration_ready,
    compute_ambient_pct,
    compute_adjustment,
    compute_anchor,
    compute_target,
)


class TestToCombined:
    """Map (sw, hw) to combined 0-200 scale."""

    def test_minimum(self):
        # sw=10 (sw_min), hw=0 -> combined=0
        assert to_combined(10, 0, sw_min=10) == 0.0

    def test_sw_full_hw_zero(self):
        # sw=100, hw=0 -> combined=100
        assert to_combined(100, 0, sw_min=10) == 100.0

    def test_maximum(self):
        # sw=100, hw=100 -> combined=200
        assert to_combined(100, 100, sw_min=10) == 200.0

    def test_sw_midpoint(self):
        # sw=55, hw=0 -> combined=50 (halfway through SW range)
        assert to_combined(55, 0, sw_min=10) == 50.0

    def test_hw_midpoint(self):
        # sw=100, hw=50 -> combined=150
        assert to_combined(100, 50, sw_min=10) == 150.0

    def test_custom_sw_min(self):
        # sw_min=20, sw=60, hw=0 -> (60-20)/(100-20)*100 = 50
        assert to_combined(60, 0, sw_min=20) == 50.0


class TestFromCombined:
    """Map combined 0-200 back to (sw, hw)."""

    def test_zero(self):
        sw, hw = from_combined(0, sw_min=10)
        assert sw == 10
        assert hw == 0

    def test_hundred(self):
        sw, hw = from_combined(100, sw_min=10)
        assert sw == 100
        assert hw == 0

    def test_two_hundred(self):
        sw, hw = from_combined(200, sw_min=10)
        assert sw == 100
        assert hw == 100

    def test_fifty(self):
        sw, hw = from_combined(50, sw_min=10)
        assert sw == 55
        assert hw == 0

    def test_one_fifty(self):
        sw, hw = from_combined(150, sw_min=10)
        assert sw == 100
        assert hw == 50

    def test_round_trip_boundaries(self):
        """Round-trip through to_combined -> from_combined preserves values."""
        for combined in [0, 25, 50, 75, 100, 125, 150, 175, 200]:
            sw, hw = from_combined(combined, sw_min=10)
            result = to_combined(sw, hw, sw_min=10)
            assert abs(result - combined) < 1.2, f"combined={combined} -> ({sw},{hw}) -> {result}"

    def test_clamp_below_zero(self):
        sw, hw = from_combined(-10, sw_min=10)
        assert sw == 10
        assert hw == 0

    def test_clamp_above_two_hundred(self):
        sw, hw = from_combined(250, sw_min=10)
        assert sw == 100
        assert hw == 100


class TestCalibrationReady:
    """Calibration requires sufficient range."""

    def test_ready_with_good_range(self):
        assert calibration_ready(50.0, 200.0) is True

    def test_not_ready_narrow_range(self):
        assert calibration_ready(100.0, 105.0) is False

    def test_not_ready_exactly_ten(self):
        # Range of exactly 10 should be ready (>=10)
        assert calibration_ready(100.0, 110.0) is True

    def test_not_ready_none_values(self):
        assert calibration_ready(None, None) is False
        assert calibration_ready(None, 100.0) is False
        assert calibration_ready(100.0, None) is False


class TestComputeAmbientPct:
    """Ambient percentage from luminance and calibration range."""

    def test_at_minimum(self):
        assert compute_ambient_pct(50.0, 50.0, 200.0) == 0.0

    def test_at_maximum(self):
        assert compute_ambient_pct(200.0, 50.0, 200.0) == 1.0

    def test_midpoint(self):
        assert compute_ambient_pct(125.0, 50.0, 200.0) == 0.5

    def test_clamp_below(self):
        assert compute_ambient_pct(10.0, 50.0, 200.0) == 0.0

    def test_clamp_above(self):
        assert compute_ambient_pct(250.0, 50.0, 200.0) == 1.0


class TestComputeAdjustment:
    """Adjustment calculation: (ambient_pct - 0.5) * range."""

    def test_neutral_fifty_percent(self):
        assert compute_adjustment(0.5, 40) == 0.0

    def test_bright_room(self):
        assert compute_adjustment(1.0, 40) == 20.0

    def test_dark_room(self):
        assert compute_adjustment(0.0, 40) == -20.0

    def test_quarter(self):
        assert compute_adjustment(0.25, 40) == -10.0


class TestComputeTarget:
    """Full target computation with clamping."""

    def test_neutral(self):
        # 50% ambient = no adjustment
        assert compute_target(100, 0.5, 40) == 100.0

    def test_bright_room(self):
        # 100% ambient, range=40, anchor=100 -> 120
        assert compute_target(100, 1.0, 40) == 120.0

    def test_dark_room(self):
        # 0% ambient, range=40, anchor=100 -> 80
        assert compute_target(100, 0.0, 40) == 80.0

    def test_clamp_high(self):
        # anchor=190, bright room -> would be 210, clamped to 200
        assert compute_target(190, 1.0, 40) == 200.0

    def test_clamp_low(self):
        # anchor=10, dark room -> would be -10, clamped to 0
        assert compute_target(10, 0.0, 40) == 0.0


class TestComputeAnchor:
    """Back-compute anchor from desired brightness + current ambient."""

    def test_inverse_of_compute_target(self):
        for pct in [0.0, 0.25, 0.5, 0.75, 1.0]:
            anchor = compute_anchor(100.0, pct, 40)
            assert compute_target(anchor, pct, 40) == pytest.approx(100.0)

    def test_neutral_ambient(self):
        # At 50% ambient, anchor == target (no adjustment)
        assert compute_anchor(100.0, 0.5, 40) == 100.0

    def test_dark_room(self):
        # Dark room: anchor must be HIGHER than target so offset lands on target
        anchor = compute_anchor(80.0, 0.0, 40)
        assert anchor == 100.0  # 80 - (0.0 - 0.5) * 40 = 80 + 20 = 100

    def test_bright_room(self):
        # Bright room: anchor must be LOWER than target
        anchor = compute_anchor(120.0, 1.0, 40)
        assert anchor == 100.0  # 120 - (1.0 - 0.5) * 40 = 120 - 20 = 100
