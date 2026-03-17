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
}

# Keys that are strings (not ints) in the config
_STRING_KEYS = {"method"}

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
