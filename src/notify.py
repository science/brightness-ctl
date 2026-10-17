"""notify-send wrapper with replace-id persistence."""

import os
from pathlib import Path


def default_notify_id_file() -> Path:
    """Per-user replace-id file in $XDG_RUNTIME_DIR (never shared /tmp, which
    collides when more than one user on the machine runs the daemon)."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime_dir) / "brightness-ctl-notify-id"


# Persist replace-id so notifications don't stack across daemon restarts
_NOTIFY_ID_FILE = default_notify_id_file()


def load_notify_id() -> int | None:
    """Load the last notification replace-id from disk."""
    try:
        text = _NOTIFY_ID_FILE.read_text().strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def save_notify_id(notify_id: int) -> None:
    """Persist notification replace-id to disk."""
    try:
        _NOTIFY_ID_FILE.write_text(str(notify_id))
    except OSError:
        pass


def build_notify_cmd(message: str, replace_id: int | None) -> list[str]:
    """Build a notify-send command list."""
    cmd = [
        "notify-send",
        "-a", "brightness-ctl",
        "--print-id",
        "-t", "1500",
        "-e",
    ]
    if replace_id is not None:
        cmd.extend(["--replace-id", str(replace_id)])
    cmd.extend(["Brightness", message])
    return cmd
