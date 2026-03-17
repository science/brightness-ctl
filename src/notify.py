"""notify-send wrapper with replace-id support."""


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
