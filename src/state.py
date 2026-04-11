"""JSON state with atomic writes (write .tmp + os.rename)."""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class AppState:
    enabled: bool = True
    offset: int = 0
    sw_brightness: int = 100
    hw_brightness: int = 0
    autobrightness_enabled: bool = False
    anchor_combined: float | None = None
    cal_min: float | None = None
    cal_max: float | None = None


def load_state(path: Path) -> AppState:
    """Load state from JSON file, returning defaults for missing file/keys."""
    if not path.exists():
        return AppState()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return AppState()

    return AppState(
        enabled=data.get("enabled", True),
        offset=data.get("offset", 0),
        sw_brightness=data.get("sw_brightness", 100),
        hw_brightness=data.get("hw_brightness", 0),
        autobrightness_enabled=data.get("autobrightness_enabled", False),
        anchor_combined=data.get("anchor_combined"),
        cal_min=data.get("cal_min"),
        cal_max=data.get("cal_max"),
    )


def save_state(state: AppState, path: Path) -> None:
    """Atomically write state to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(asdict(state), indent=2) + "\n")
    os.rename(tmp_path, path)
