"""Brightness state machine — pure logic, no I/O."""

from dataclasses import dataclass
from enum import Enum, auto


class Action(Enum):
    APPLY_SW = auto()  # Need to re-apply gammastep (software brightness changed)
    APPLY_HW = auto()  # Need to call ddcutil (hardware brightness changed)
    NONE = auto()      # Already at limit, nothing to do


@dataclass
class BrightnessState:
    sw_brightness: int  # 10-100 (maps to 0.10-1.00)
    hw_brightness: int  # 0-100 (DDC/CI percentage)


def bright_up(state: BrightnessState, config: dict) -> tuple[BrightnessState, Action]:
    """Increase brightness: SW first (back to 1.0), then HW."""
    sw = state.sw_brightness
    hw = state.hw_brightness

    if sw < 100:
        sw = min(sw + config["sw_step"], 100)
        return BrightnessState(sw, hw), Action.APPLY_SW
    elif hw < 100:
        hw = min(hw + config["hw_step"], 100)
        return BrightnessState(sw, hw), Action.APPLY_HW
    else:
        return BrightnessState(sw, hw), Action.NONE


def bright_down(state: BrightnessState, config: dict) -> tuple[BrightnessState, Action]:
    """Decrease brightness: HW first (to 0%), then SW."""
    sw = state.sw_brightness
    hw = state.hw_brightness
    sw_min = config["sw_min"]

    if hw > 0:
        hw = max(hw - config["hw_step"], 0)
        return BrightnessState(sw, hw), Action.APPLY_HW
    elif sw > sw_min:
        sw = max(sw - config["sw_step"], sw_min)
        return BrightnessState(sw, hw), Action.APPLY_SW
    else:
        return BrightnessState(sw, hw), Action.NONE
