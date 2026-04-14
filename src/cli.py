"""Socket client: connect to daemon, send JSON command, print response."""

import json
import socket
import sys


def send_command(socket_path: str, cmd: str, args: dict | None = None) -> dict:
    """Connect to daemon socket, send command, return response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(5.0)
        sock.connect(socket_path)
        request_dict = {"cmd": cmd}
        if args is not None:
            request_dict["args"] = args
        request = json.dumps(request_dict) + "\n"
        sock.sendall(request.encode())

        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk

        return json.loads(data.decode().strip())
    finally:
        sock.close()


def format_status(resp: dict) -> str:
    """Format a status response for display."""
    lines = [
        f"Enabled:     {resp.get('enabled', '?')}",
        f"Base temp:   {resp.get('base_temp', '?')}K",
        f"Offset:      {resp.get('offset', '?')}K",
        f"Applied:     {resp.get('applied_temp', '?')}K",
        f"Day temp:    {resp.get('day_temp', '?')}K",
        f"Night temp:  {resp.get('night_temp', '?')}K",
        f"HW bright:   {resp.get('hw_brightness', '?')}%",
        f"SW bright:   {resp.get('sw_brightness', 100) / 100:.2f}",
    ]
    return "\n".join(lines)


def format_auto_status(resp: dict) -> str:
    """Format an auto-status response for display."""
    enabled = resp.get("autobrightness_enabled", False)
    anchor = resp.get("anchor_combined")
    cal_min = resp.get("cal_min")
    cal_max = resp.get("cal_max")
    cal_ready = resp.get("calibration_ready", False)
    lines = [
        f"Auto-brightness: {'ON' if enabled else 'OFF'}",
        f"Anchor:          {anchor if anchor is not None else 'not set'}",
        f"Calibration:     {'ready' if cal_ready else 'not ready'}",
        f"  cal_min:       {cal_min if cal_min is not None else 'N/A'}",
        f"  cal_max:       {cal_max if cal_max is not None else 'N/A'}",
    ]
    return "\n".join(lines)


def main(socket_path: str, args: list[str]) -> int:
    """CLI entry point. Returns exit code."""
    if not args:
        args = ["help"]

    cmd = args[0]

    if cmd in ("help", "--help", "-h"):
        print("Usage: brightness-ctl <command>")
        print()
        print("Color temperature:")
        print("  warmer (w)       Shift warmer (more red)")
        print("  cooler (c)       Shift cooler (less red)")
        print("  toggle (t)       Toggle color shift on/off")
        print("  reset  (r)       Reset offset to zero")
        print()
        print("Brightness:")
        print("  bright-up   (bu) Increase brightness (SW up to 1.0, then HW)")
        print("  bright-down (bd) Decrease brightness (HW down to 0%, then SW)")
        print()
        print("Auto-brightness:")
        print("  auto-on         (ao)  Enable, set anchor from current brightness")
        print("  auto-off        (af)  Disable, keep current brightness")
        print("  auto-status     (as)  Show calibration and anchor status")
        print("  auto-calibrate  (ac)  Recompute calibration from logs")
        print("  auto-reset-cal  (arc) Clear calibration + delete logs")
        print("  auto-set-cal    (asc) Manually set cal_min and cal_max")
        print()
        print("System:")
        print("  status (s)       Show current status")
        print("  daemon (d)       Start background daemon")
        print("  stop             Stop background daemon")
        print("  help             Show this help")
        return 0

    # Expand aliases
    aliases = {
        "w": "warmer", "c": "cooler", "t": "toggle", "r": "reset",
        "bu": "bright-up", "bd": "bright-down", "s": "status", "d": "daemon",
        "ao": "auto-on", "af": "auto-off", "as": "auto-status",
        "ac": "auto-calibrate", "arc": "auto-reset-cal",
        "asc": "auto-set-cal",
    }
    cmd = aliases.get(cmd, cmd)

    if cmd == "daemon":
        return None  # Signal to caller to start daemon instead

    send_args = None
    if cmd == "auto-set-cal":
        if len(args) < 3:
            print("Usage: brightness-ctl auto-set-cal <min> <max>", file=sys.stderr)
            return 1
        try:
            send_args = {"cal_min": float(args[1]), "cal_max": float(args[2])}
        except ValueError:
            print("Error: min and max must be numbers", file=sys.stderr)
            return 1

    try:
        resp = send_command(socket_path, cmd, args=send_args)
    except FileNotFoundError:
        print("Error: daemon not running (socket not found)", file=sys.stderr)
        return 1
    except ConnectionRefusedError:
        print("Error: daemon not running (connection refused)", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if cmd == "status":
        print(format_status(resp))
    elif cmd in ("auto-status", "auto-set-cal", "auto-calibrate"):
        if resp.get("status") == "error":
            print(f"Error: {resp.get('message', 'unknown')}", file=sys.stderr)
            return 1
        print(format_auto_status(resp))
    elif resp.get("status") == "error":
        print(f"Error: {resp.get('message', 'unknown')}", file=sys.stderr)
        return 1

    return 0
