"""Auto-brightness pure math — combined scale, calibration, ambient adjustment."""


def to_combined(sw: int, hw: int, sw_min: int = 10) -> float:
    """Map (sw, hw) to combined 0-200 scale.

    0 = sw at sw_min, hw at 0 (dimmest)
    100 = sw at 100, hw at 0
    200 = sw at 100, hw at 100 (brightest)
    """
    if hw > 0:
        return 100.0 + hw
    return (sw - sw_min) / (100 - sw_min) * 100.0


def from_combined(combined: float, sw_min: int = 10) -> tuple[int, int]:
    """Map combined 0-200 back to (sw, hw). Clamps to valid range."""
    combined = max(0.0, min(200.0, combined))
    if combined <= 100.0:
        sw = round(sw_min + combined / 100.0 * (100 - sw_min))
        return sw, 0
    else:
        hw = round(combined - 100.0)
        return 100, hw


def calibration_ready(cal_min, cal_max) -> bool:
    """True when calibration range is sufficient (>= 10 luminance units)."""
    if cal_min is None or cal_max is None:
        return False
    return (cal_max - cal_min) >= 10


def compute_ambient_pct(luminance: float, cal_min: float, cal_max: float) -> float:
    """Compute ambient percentage from luminance, clamped 0.0-1.0."""
    if cal_max == cal_min:
        return 0.5
    pct = (luminance - cal_min) / (cal_max - cal_min)
    return max(0.0, min(1.0, pct))


def compute_adjustment(ambient_pct: float, autobrightness_range: float) -> float:
    """Compute brightness adjustment. 50% ambient = no adjustment."""
    return (ambient_pct - 0.5) * autobrightness_range


def compute_target(anchor: float, ambient_pct: float, autobrightness_range: float) -> float:
    """Compute target combined brightness, clamped 0-200."""
    adjustment = compute_adjustment(ambient_pct, autobrightness_range)
    target = anchor + adjustment
    return max(0.0, min(200.0, target))
