"""Tests for luminance_log.py — JSONL logging, calibration from logs, rotation."""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from luminance_log import (
    append_reading,
    load_readings,
    compute_calibration,
    rotate_logs,
)


class TestAppendReading:
    """Append luminance readings as JSONL to daily log files."""

    def test_creates_log_file(self, tmp_path):
        append_reading(tmp_path, 142.5)
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"luminance-{today}.log"
        assert log_file.exists()

    def test_appends_jsonl(self, tmp_path):
        append_reading(tmp_path, 100.0)
        append_reading(tmp_path, 150.0)
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"luminance-{today}.log"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        rec1 = json.loads(lines[0])
        rec2 = json.loads(lines[1])
        assert rec1["luminance"] == 100.0
        assert rec2["luminance"] == 150.0

    def test_record_has_timestamp(self, tmp_path):
        append_reading(tmp_path, 80.0)
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"luminance-{today}.log"
        rec = json.loads(log_file.read_text().strip())
        assert "timestamp" in rec
        # Should be a valid ISO timestamp
        datetime.fromisoformat(rec["timestamp"])

    def test_creates_log_dir(self, tmp_path):
        log_dir = tmp_path / "subdir" / "logs"
        append_reading(log_dir, 100.0)
        assert log_dir.exists()


class TestLoadReadings:
    """Load and sort readings from multiple daily log files."""

    def test_load_single_file(self, tmp_path):
        append_reading(tmp_path, 100.0)
        append_reading(tmp_path, 200.0)
        readings = load_readings(tmp_path, lookback_days=7)
        assert len(readings) == 2

    def test_load_empty_dir(self, tmp_path):
        readings = load_readings(tmp_path, lookback_days=7)
        assert readings == []

    def test_load_respects_lookback(self, tmp_path):
        # Create a file dated 30 days ago
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        old_file = tmp_path / f"luminance-{old_date}.log"
        old_file.write_text(json.dumps({"timestamp": f"{old_date}T12:00:00", "luminance": 50.0}) + "\n")

        # Create today's file
        append_reading(tmp_path, 100.0)

        readings = load_readings(tmp_path, lookback_days=7)
        assert len(readings) == 1
        assert readings[0]["luminance"] == 100.0

    def test_sorted_by_timestamp(self, tmp_path):
        # Write records out of order across files
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        yfile = tmp_path / f"luminance-{yesterday}.log"
        yfile.write_text(json.dumps({"timestamp": f"{yesterday}T23:00:00", "luminance": 200.0}) + "\n")

        append_reading(tmp_path, 100.0)

        readings = load_readings(tmp_path, lookback_days=7)
        assert len(readings) == 2
        assert readings[0]["luminance"] == 200.0  # yesterday first
        assert readings[1]["luminance"] == 100.0  # today second

    def test_skips_malformed_lines(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"luminance-{today}.log"
        log_file.write_text("not json\n" + json.dumps({"timestamp": f"{today}T12:00:00", "luminance": 50.0}) + "\n")
        readings = load_readings(tmp_path, lookback_days=7)
        assert len(readings) == 1


class TestComputeCalibration:
    """Percentile-based calibration from readings."""

    def _make_readings(self, luminances):
        """Helper: create reading dicts from luminance values."""
        return [{"luminance": v} for v in luminances]

    def test_basic_calibration(self):
        # 100 readings from 0 to 99
        readings = self._make_readings(range(100))
        result = compute_calibration(readings, pct_lo=5, pct_hi=95)
        assert result is not None
        cal_min, cal_max = result
        # 5th percentile of 0-99 ≈ 4.95, 95th ≈ 94.05
        assert 3 <= cal_min <= 6
        assert 93 <= cal_max <= 96

    def test_insufficient_data(self):
        readings = self._make_readings([50.0] * 29)
        result = compute_calibration(readings, pct_lo=5, pct_hi=95)
        assert result is None  # not enough data (<30 readings)

    def test_thirty_readings_sufficient(self):
        readings = self._make_readings(range(0, 60, 2))  # 30 values: 0,2,4,...,58
        result = compute_calibration(readings, pct_lo=5, pct_hi=95)
        assert result is not None

    def test_narrow_range_returns_none(self):
        # 100 readings but all similar values (range < 10)
        readings = self._make_readings([100.0 + i * 0.05 for i in range(100)])
        result = compute_calibration(readings, pct_lo=5, pct_hi=95)
        assert result is None

    def test_outliers_clipped(self):
        # 98 readings around 100, plus outliers at 0 and 255
        readings = self._make_readings(
            [0.0] * 2 + [100.0 + i for i in range(98)] + [255.0] * 2
        )
        result = compute_calibration(readings, pct_lo=5, pct_hi=95)
        assert result is not None
        cal_min, cal_max = result
        # Outliers (0, 255) should be clipped
        assert cal_min > 5
        assert cal_max < 250

    def test_boundary_at_thirty(self):
        readings = self._make_readings(range(30))
        result = compute_calibration(readings, pct_lo=5, pct_hi=95)
        assert result is not None


class TestRotateLogs:
    """Delete luminance logs older than retention period."""

    def test_deletes_old_logs(self, tmp_path):
        old_date = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        old_file = tmp_path / f"luminance-{old_date}.log"
        old_file.write_text("{}\n")

        rotate_logs(tmp_path, retention_days=90)
        assert not old_file.exists()

    def test_keeps_recent_logs(self, tmp_path):
        today = datetime.now().strftime("%Y-%m-%d")
        recent_file = tmp_path / f"luminance-{today}.log"
        recent_file.write_text("{}\n")

        rotate_logs(tmp_path, retention_days=90)
        assert recent_file.exists()

    def test_handles_empty_dir(self, tmp_path):
        # Should not raise
        rotate_logs(tmp_path, retention_days=90)

    def test_ignores_non_log_files(self, tmp_path):
        other = tmp_path / "config.toml"
        other.write_text("key = 1\n")
        rotate_logs(tmp_path, retention_days=0)
        assert other.exists()
