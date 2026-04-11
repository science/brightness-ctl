"""V4L2 camera capture via ctypes — ambient light sensing."""

import ctypes
import ctypes.util
import fcntl
import mmap
import os
import struct
from dataclasses import dataclass, field


# --- Pure extraction (testable without hardware) ---

def extract_luminance_from_yuyv(frame: bytes, width: int, height: int) -> float:
    """Extract average Y channel luminance from a YUYV frame buffer.

    YUYV format: each 4 bytes encodes 2 pixels as [Y0, U, Y1, V].
    We only care about Y (luminance), skipping U and V (chroma).
    """
    total = 0
    n_pixels = width * height
    # Y values are at byte offsets 0, 2, 4, 6, ... (every other byte)
    for i in range(0, len(frame), 2):
        total += frame[i]
    if n_pixels == 0:
        return 0.0
    return total / n_pixels


# --- V4L2 ioctls via ctypes (requires real /dev/videoN) ---

# V4L2 ioctl numbers
VIDIOC_QUERYCAP = 0x80685600
VIDIOC_S_FMT = 0xC0D05605
VIDIOC_REQBUFS = 0xC0145608
VIDIOC_QUERYBUF = 0xC0585609
VIDIOC_QBUF = 0xC058560F
VIDIOC_DQBUF = 0xC0585611
VIDIOC_STREAMON = 0x40045612
VIDIOC_STREAMOFF = 0x40045613

# V4L2 pixel formats
V4L2_PIX_FMT_YUYV = 0x56595559

# V4L2 buffer types and memory types
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_MEMORY_MMAP = 1

# Buffer count for mmap
NUM_BUFFERS = 2


class v4l2_format(ctypes.Structure):
    """Simplified v4l2_format for video capture."""
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("fmt_pix_width", ctypes.c_uint32),
        ("fmt_pix_height", ctypes.c_uint32),
        ("fmt_pix_pixelformat", ctypes.c_uint32),
        ("fmt_pix_field", ctypes.c_uint32),
        ("fmt_pix_bytesperline", ctypes.c_uint32),
        ("fmt_pix_sizeimage", ctypes.c_uint32),
        ("fmt_pix_colorspace", ctypes.c_uint32),
        ("fmt_pix_priv", ctypes.c_uint32),
        ("fmt_pix_flags", ctypes.c_uint32),
        ("_padding", ctypes.c_uint8 * 168),
    ]


class v4l2_requestbuffers(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("flags", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 3),
    ]


class v4l2_buffer(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("bytesused", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        ("tv_sec", ctypes.c_long),
        ("tv_usec", ctypes.c_long),
        ("timecode_type", ctypes.c_uint32),
        ("timecode_flags", ctypes.c_uint32),
        ("timecode_frames", ctypes.c_uint8),
        ("timecode_seconds", ctypes.c_uint8),
        ("timecode_minutes", ctypes.c_uint8),
        ("timecode_hours", ctypes.c_uint8),
        ("timecode_userbits", ctypes.c_uint8 * 4),
        ("sequence", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("m_offset", ctypes.c_uint32),
        ("length", ctypes.c_uint32),
        ("reserved2", ctypes.c_uint32),
        ("request_fd_or_reserved", ctypes.c_int32),
    ]


@dataclass
class CameraHandle:
    """Open camera state: fd, mmap buffers."""
    fd: int
    width: int
    height: int
    buffers: list = field(default_factory=list)  # list of mmap objects
    buf_lengths: list = field(default_factory=list)


def _ioctl(fd, request, arg):
    """Call ioctl, raise OSError on failure."""
    ret = fcntl.ioctl(fd, request, arg)
    if ret < 0:
        raise OSError(f"ioctl 0x{request:08X} failed with {ret}")
    return ret


def open_camera(device: str, width: int = 160, height: int = 120) -> CameraHandle:
    """Open V4L2 camera device and set up YUYV capture with mmap buffers."""
    fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)

    try:
        # Set format: YUYV 160x120
        fmt = v4l2_format()
        fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        fmt.fmt_pix_width = width
        fmt.fmt_pix_height = height
        fmt.fmt_pix_pixelformat = V4L2_PIX_FMT_YUYV
        _ioctl(fd, VIDIOC_S_FMT, fmt)

        actual_w = fmt.fmt_pix_width
        actual_h = fmt.fmt_pix_height

        # Request mmap buffers
        req = v4l2_requestbuffers()
        req.count = NUM_BUFFERS
        req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        req.memory = V4L2_MEMORY_MMAP
        _ioctl(fd, VIDIOC_REQBUFS, req)

        buffers = []
        buf_lengths = []

        for i in range(req.count):
            buf = v4l2_buffer()
            buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
            buf.memory = V4L2_MEMORY_MMAP
            buf.index = i
            _ioctl(fd, VIDIOC_QUERYBUF, buf)

            mm = mmap.mmap(fd, buf.length, offset=buf.m_offset)
            buffers.append(mm)
            buf_lengths.append(buf.length)

            # Queue the buffer
            _ioctl(fd, VIDIOC_QBUF, buf)

        # Start streaming
        buf_type = ctypes.c_uint32(V4L2_BUF_TYPE_VIDEO_CAPTURE)
        _ioctl(fd, VIDIOC_STREAMON, buf_type)

        return CameraHandle(
            fd=fd, width=actual_w, height=actual_h,
            buffers=buffers, buf_lengths=buf_lengths,
        )
    except Exception:
        os.close(fd)
        raise


def capture_luminance(handle: CameraHandle, num_frames: int = 4) -> float:
    """Capture multiple frames and return average luminance (0-255).

    Discards first frame after stream-on (warmup zeros).
    """
    import select

    luminances = []
    frames_captured = 0
    frames_to_capture = num_frames + 1  # +1 for warmup discard

    while frames_captured < frames_to_capture:
        # Wait for frame ready
        ready, _, _ = select.select([handle.fd], [], [], 5.0)
        if not ready:
            break

        # Dequeue buffer
        buf = v4l2_buffer()
        buf.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        buf.memory = V4L2_MEMORY_MMAP
        _ioctl(handle.fd, VIDIOC_DQBUF, buf)

        frames_captured += 1

        if frames_captured > 1:  # skip first frame (warmup)
            frame_data = handle.buffers[buf.index][:buf.bytesused]
            lum = extract_luminance_from_yuyv(frame_data, handle.width, handle.height)
            luminances.append(lum)

        # Re-queue buffer
        _ioctl(handle.fd, VIDIOC_QBUF, buf)

    if not luminances:
        return 0.0
    return sum(luminances) / len(luminances)


def close_camera(handle: CameraHandle) -> None:
    """Stop streaming and close camera device."""
    try:
        buf_type = ctypes.c_uint32(V4L2_BUF_TYPE_VIDEO_CAPTURE)
        _ioctl(handle.fd, VIDIOC_STREAMOFF, buf_type)
    except OSError:
        pass
    for mm in handle.buffers:
        try:
            mm.close()
        except Exception:
            pass
    os.close(handle.fd)
