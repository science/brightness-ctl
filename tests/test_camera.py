"""Tests for camera.py — V4L2 luminance extraction with synthetic YUYV data."""

import struct

import pytest

from camera import extract_luminance_from_yuyv


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
