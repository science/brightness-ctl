"""JSONL luminance logging + calibration from logs."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path


def append_reading(log_dir: Path, luminance: float) -> None:
    """Append a luminance reading to today's JSONL log file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"luminance-{today}.log"
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "luminance": luminance,
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_readings(log_dir: Path, lookback_days: int = 7) -> list[dict]:
    """Load readings from JSONL log files within lookback window, sorted by timestamp."""
    if not log_dir.exists():
        return []

    cutoff = datetime.now() - timedelta(days=lookback_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    readings = []

    for log_file in sorted(log_dir.glob("luminance-*.log")):
        # Extract date from filename
        match = re.search(r"luminance-(\d{4}-\d{2}-\d{2})\.log$", log_file.name)
        if not match:
            continue
        file_date = match.group(1)
        if file_date < cutoff_str:
            continue

        for line in log_file.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                rec = json.loads(line)
                readings.append(rec)
            except json.JSONDecodeError:
                continue

    readings.sort(key=lambda r: r.get("timestamp", ""))
    return readings


def compute_calibration(
    readings: list[dict], pct_lo: int = 5, pct_hi: int = 95
) -> tuple[float, float] | None:
    """Compute calibration min/max using percentile clipping.

    Returns (cal_min, cal_max) or None if insufficient data or range.
    Requires >= 100 readings and percentile range >= 10.
    """
    if len(readings) < 100:
        return None

    values = sorted(r["luminance"] for r in readings)
    n = len(values)

    lo_idx = int(n * pct_lo / 100)
    hi_idx = int(n * pct_hi / 100) - 1
    hi_idx = max(hi_idx, lo_idx)

    cal_min = values[lo_idx]
    cal_max = values[hi_idx]

    if cal_max - cal_min < 10:
        return None

    return cal_min, cal_max


def rotate_logs(log_dir: Path, retention_days: int = 90) -> None:
    """Delete luminance log files older than retention period."""
    if not log_dir.exists():
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    for log_file in log_dir.glob("luminance-*.log"):
        match = re.search(r"luminance-(\d{4}-\d{2}-\d{2})\.log$", log_file.name)
        if not match:
            continue
        if match.group(1) < cutoff_str:
            log_file.unlink()
