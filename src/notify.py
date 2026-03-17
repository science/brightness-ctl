"""notify-send wrapper with replace-id persistence."""

from pathlib import Path

# Persist replace-id so notifications don't stack across daemon restarts
_NOTIFY_ID_FILE = Path("/tmp/brightness-ctl-notify-id")


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
