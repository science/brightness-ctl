"""Tests for camera.py — V4L2 luminance extraction and safe device selection."""

import struct
from pathlib import Path

import pytest

from camera import (
    ALCOR_AMBIENT_VIDPID,
    BLOCKED_VIDPIDS,
    CameraError,
    extract_luminance_from_yuyv,
    resolve_camera_device,
    scan_v4l2_devices,
    select_camera_device,
)


ALCOR = ALCOR_AMBIENT_VIDPID           # ("058f", "5608")
LOGITECH_C615 = ("046d", "082c")        # MUST stay in BLOCKED_VIDPIDS


class TestExtractLuminance:
    """Extract average Y channel from YUYV frame data."""

    def test_uniform_black(self):
        """All-zero YUYV frame -> luminance 0."""
        # YUYV: each 4 bytes = Y0 U Y1 V, for 2 pixels
        width, height = 160, 120
        frame = bytes(width * height * 2)  # all zeros
        assert extract_luminance_from_yuyv(frame, width, height) == 0.0

    def test_uniform_white(self):
        """All Y=255 YUYV frame -> luminance 255."""
        width, height = 160, 120
        # Y0=255, U=128, Y1=255, V=128 (white in YUYV)
        pixel_pair = bytes([255, 128, 255, 128])
        frame = pixel_pair * (width * height // 2)
        result = extract_luminance_from_yuyv(frame, width, height)
        assert result == 255.0

    def test_uniform_mid_gray(self):
        """Y=128 everywhere -> luminance 128."""
        width, height = 160, 120
        pixel_pair = bytes([128, 128, 128, 128])
        frame = pixel_pair * (width * height // 2)
        result = extract_luminance_from_yuyv(frame, width, height)
        assert result == 128.0

    def test_mixed_luminance(self):
        """Half Y=0, half Y=200 -> average 100."""
        width, height = 4, 2  # small frame for simplicity
        # First row: Y=0
        row_dark = bytes([0, 128, 0, 128]) * (width // 2)
        # Second row: Y=200
        row_bright = bytes([200, 128, 200, 128]) * (width // 2)
        frame = row_dark + row_bright
        result = extract_luminance_from_yuyv(frame, width, height)
        assert result == 100.0

    def test_small_frame(self):
        """2x1 frame (minimum: one YUYV macro-pixel)."""
        frame = bytes([50, 128, 100, 128])  # Y0=50, Y1=100
        result = extract_luminance_from_yuyv(frame, 2, 1)
        assert result == 75.0


class TestBlocklistConstants:
    """The Logitech C615 meeting camera MUST always be blocklisted."""

    def test_c615_is_blocked(self):
        assert LOGITECH_C615 in BLOCKED_VIDPIDS

    def test_alcor_is_not_blocked(self):
        assert ALCOR not in BLOCKED_VIDPIDS


class TestSelectCameraDevice:
    """Pure selection logic — given a scan result, pick a node or refuse."""

    def _entry(self, node, vid, pid):
        return {"node": node, "vid": vid, "pid": pid}

    def test_no_hint_single_alcor_match(self):
        entries = [self._entry("/dev/video0", "058f", "5608")]
        assert select_camera_device(entries, None, ALCOR, BLOCKED_VIDPIDS) == "/dev/video0"

    def test_no_hint_alcor_and_logitech_present_picks_alcor(self):
        """Logitech at video0, Alcor at video2 — must pick Alcor."""
        entries = [
            self._entry("/dev/video0", "046d", "082c"),
            self._entry("/dev/video1", "046d", "082c"),
            self._entry("/dev/video2", "058f", "5608"),
            self._entry("/dev/video3", "058f", "5608"),
        ]
        assert select_camera_device(entries, None, ALCOR, BLOCKED_VIDPIDS) == "/dev/video2"

    def test_no_hint_flipped_enumeration_picks_alcor(self):
        """The inverted-node scenario from HOST_RESULTS.md: Alcor at 0, Logitech at 2."""
        entries = [
            self._entry("/dev/video0", "058f", "5608"),
            self._entry("/dev/video1", "058f", "5608"),
            self._entry("/dev/video2", "046d", "082c"),
            self._entry("/dev/video3", "046d", "082c"),
        ]
        assert select_camera_device(entries, None, ALCOR, BLOCKED_VIDPIDS) == "/dev/video0"

    def test_no_hint_no_alcor_raises(self):
        """Only Logitech present — refuse rather than fall back."""
        entries = [self._entry("/dev/video0", "046d", "082c")]
        with pytest.raises(CameraError, match="no allowed"):
            select_camera_device(entries, None, ALCOR, BLOCKED_VIDPIDS)

    def test_no_hint_empty_scan_raises(self):
        with pytest.raises(CameraError, match="no video devices"):
            select_camera_device([], None, ALCOR, BLOCKED_VIDPIDS)

    def test_hint_matches_alcor(self):
        entries = [
            self._entry("/dev/video0", "058f", "5608"),
            self._entry("/dev/video2", "046d", "082c"),
        ]
        assert select_camera_device(entries, "/dev/video0", ALCOR, BLOCKED_VIDPIDS) == "/dev/video0"

    def test_hint_points_at_blocked_device_raises(self):
        """Explicit config pointing at the Logitech must be refused."""
        entries = [
            self._entry("/dev/video0", "058f", "5608"),
            self._entry("/dev/video2", "046d", "082c"),
        ]
        with pytest.raises(CameraError, match="blocked"):
            select_camera_device(entries, "/dev/video2", ALCOR, BLOCKED_VIDPIDS)

    def test_hint_not_in_scan_raises(self):
        """Can't verify safety of unknown node — refuse."""
        entries = [self._entry("/dev/video0", "058f", "5608")]
        with pytest.raises(CameraError, match="not found"):
            select_camera_device(entries, "/dev/video9", ALCOR, BLOCKED_VIDPIDS)

    def test_hint_points_at_wrong_but_not_blocked_allowed(self):
        """Unusual: user explicitly picks a non-Alcor, non-Logitech node. Allow."""
        entries = [
            self._entry("/dev/video0", "1234", "5678"),
            self._entry("/dev/video2", "058f", "5608"),
        ]
        assert select_camera_device(entries, "/dev/video0", ALCOR, BLOCKED_VIDPIDS) == "/dev/video0"


def _make_fake_sysfs(tmp_path: Path, devices: list[dict]) -> Path:
    """Build a fake /sys/class/video4linux tree for scan tests.

    devices: [{"name": "video0", "vid": "058f", "pid": "5608"}, ...]
    """
    sysfs_class = tmp_path / "sys" / "class" / "video4linux"
    sysfs_class.mkdir(parents=True)
    bus = tmp_path / "sys" / "bus" / "usb" / "devices"
    bus.mkdir(parents=True)

    for i, dev in enumerate(devices):
        # Create a fake USB device dir with idVendor/idProduct
        usb_dev = bus / f"1-{i+1}"
        usb_dev.mkdir()
        (usb_dev / "idVendor").write_text(dev["vid"] + "\n")
        (usb_dev / "idProduct").write_text(dev["pid"] + "\n")
        # USB interface subdir (where video4linux device actually links to)
        iface = usb_dev / f"1-{i+1}:1.0"
        iface.mkdir()
        # /sys/class/video4linux/videoN/device -> iface
        video_entry = sysfs_class / dev["name"]
        video_entry.mkdir()
        (video_entry / "device").symlink_to(iface)

    return sysfs_class


class TestScanV4L2Devices:
    """Reads /sys/class/video4linux and returns node/vid/pid tuples."""

    def test_empty_sysfs(self, tmp_path):
        sysfs = _make_fake_sysfs(tmp_path, [])
        assert scan_v4l2_devices(sysfs) == []

    def test_single_alcor(self, tmp_path):
        sysfs = _make_fake_sysfs(tmp_path, [
            {"name": "video0", "vid": "058f", "pid": "5608"},
        ])
        result = scan_v4l2_devices(sysfs)
        assert result == [{"node": "/dev/video0", "vid": "058f", "pid": "5608"}]

    def test_multiple_devices_sorted_by_node(self, tmp_path):
        sysfs = _make_fake_sysfs(tmp_path, [
            {"name": "video2", "vid": "046d", "pid": "082c"},
            {"name": "video0", "vid": "058f", "pid": "5608"},
            {"name": "video1", "vid": "058f", "pid": "5608"},
            {"name": "video3", "vid": "046d", "pid": "082c"},
        ])
        result = scan_v4l2_devices(sysfs)
        nodes = [r["node"] for r in result]
        assert nodes == ["/dev/video0", "/dev/video1", "/dev/video2", "/dev/video3"]
        assert result[0]["vid"] == "058f"
        assert result[2]["vid"] == "046d"

    def test_missing_sysfs_returns_empty(self, tmp_path):
        assert scan_v4l2_devices(tmp_path / "nope") == []

    def test_video_entry_without_device_link_skipped(self, tmp_path):
        sysfs = tmp_path / "sys" / "class" / "video4linux"
        sysfs.mkdir(parents=True)
        (sysfs / "video0").mkdir()  # no device symlink
        assert scan_v4l2_devices(sysfs) == []


class TestResolveCameraDevice:
    """End-to-end resolver: scan sysfs + apply selection."""

    def test_hostlike_logitech_at_video2(self, tmp_path):
        """Exact HOST_RESULTS.md scenario: Alcor at 0/1, Logitech at 2/3, hint=None."""
        sysfs = _make_fake_sysfs(tmp_path, [
            {"name": "video0", "vid": "058f", "pid": "5608"},
            {"name": "video1", "vid": "058f", "pid": "5608"},
            {"name": "video2", "vid": "046d", "pid": "082c"},
            {"name": "video3", "vid": "046d", "pid": "082c"},
        ])
        assert resolve_camera_device(None, sysfs_root=sysfs) == "/dev/video0"

    def test_hint_at_logitech_refused(self, tmp_path):
        """If old config still says /dev/video2 and that's the Logitech, refuse."""
        sysfs = _make_fake_sysfs(tmp_path, [
            {"name": "video0", "vid": "058f", "pid": "5608"},
            {"name": "video2", "vid": "046d", "pid": "082c"},
        ])
        with pytest.raises(CameraError, match="blocked"):
            resolve_camera_device("/dev/video2", sysfs_root=sysfs)

    def test_no_camera_present_raises(self, tmp_path):
        sysfs = _make_fake_sysfs(tmp_path, [])
        with pytest.raises(CameraError):
            resolve_camera_device(None, sysfs_root=sysfs)
