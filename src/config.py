"""TOML config loading with defaults and bash config migration."""

import tomllib
from pathlib import Path


DEFAULT_CONFIG = {
    "day_temp": 2800,
    "night_temp": 2200,
    "step": 200,
    "min_temp": 1500,
    "max_temp": 6500,
    "dawn_start": 6,
    "dawn_end": 8,
    "dusk_start": 18,
    "dusk_end": 20,
    "method": "randr",
    "hw_step": 5,
    "sw_step": 5,
    "sw_min": 10,
    "autobrightness_range": 40,
    "autobrightness_interval": 60,
    "luminance_log_interval": 1800,
    # None = auto-probe via resolve_camera_device (recommended).
    # Set to an explicit /dev/videoN only if you know what you're doing;
    # the resolver will still refuse devices on the BLOCKED_VIDPIDS list.
    "camera_device": None,
    "camera_frames": 4,
    # V4L2_CID_BRIGHTNESS value applied after open. None = don't touch.
    # On the Alcor 058f:5608 ambient module the factory default is 0 which
    # produces near-black frames; 32 shifts the operating point into a
    # usable dynamic range. Tune per sensor.
    "camera_brightness": None,
    "calibration_lookback_days": 7,
    "calibration_percentile_lo": 5,
    "calibration_percentile_hi": 95,
    "log_retention_days": 90,
    # On Cinnamon ScreenSaver.ActiveChanged=True, drive all monitors to
    # the chosen DPMS state (default "standby" — "off" can fail to wake
    # reliably after suspend/resume on some GPUs).
    "screensaver_monitor_off": True,
    "screensaver_dpms_mode": "standby",
}

# Keys that are strings (not ints) in the config
_STRING_KEYS = {"method", "camera_device", "screensaver_dpms_mode"}

# Mapping from bash VAR_NAME to toml key_name
_BASH_KEY_MAP = {k.upper(): k for k in DEFAULT_CONFIG}


def load_config(path: Path) -> dict:
    """Load TOML config, falling back to defaults for missing keys."""
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        with open(path, "rb") as f:
            user_config = tomllib.load(f)
        config.update(user_config)
    return config


def migrate_bash_config(bash_path: Path, toml_path: Path) -> bool:
    """Migrate old bash-style KEY=VALUE config to TOML. Returns True if migrated."""
    if not bash_path.exists() or toml_path.exists():
        return False

    values = {}
    with open(bash_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # Strip inline comments (e.g. "2800  # Daytime temperature")
            if "#" in val:
                val = val[:val.index("#")].strip()
            toml_key = _BASH_KEY_MAP.get(key)
            if toml_key is not None:
                if toml_key in _STRING_KEYS:
                    values[toml_key] = val
                else:
                    try:
                        values[toml_key] = int(val)
                    except ValueError:
                        values[toml_key] = val

    lines = []
    for k, v in values.items():
        if isinstance(v, str):
            lines.append(f'{k} = "{v}"')
        else:
            lines.append(f"{k} = {v}")

    toml_path.write_text("\n".join(lines) + "\n")
    return True
