"""V4L2 camera capture via ctypes — ambient light sensing.

## Device-selection safety

V4L2 device-node numbers (/dev/video0, /dev/video1, ...) are assigned by
uvcvideo in USB probe order and are NOT stable across reboots or hotplugs.
Older versions of this module hard-coded `/dev/video2` as "the Alcor ambient
sensor", which was wrong: on some boots the Logitech C615 meeting camera
landed at /dev/video2 instead. Opening the meeting cam would be a privacy
incident.

The selection rule is now:

  - Scan /sys/class/video4linux and look up each node's USB VID:PID.
  - Pick the device whose VID:PID matches ALCOR_AMBIENT_VIDPID (058f:5608).
  - REFUSE to open any device whose VID:PID is in BLOCKED_VIDPIDS, even if
    the user explicitly configured that node in config.toml.

Never remove the Logitech C615 (046d:082c) from BLOCKED_VIDPIDS.
"""

import ctypes
import ctypes.util
import fcntl
import mmap
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path


# --- Device selection (VID:PID based, node-number independent) ---

ALCOR_AMBIENT_VIDPID: tuple[str, str] = ("058f", "5608")

BLOCKED_VIDPIDS: set[tuple[str, str]] = {
    ("046d", "082c"),  # Logitech HD Webcam C615 — user's meeting camera
}

_DEFAULT_SYSFS_ROOT = Path("/sys/class/video4linux")


class CameraError(Exception):
    """Raised when no safe camera can be resolved."""


def _read_usb_vidpid(video_sysfs_entry: Path) -> tuple[str, str] | None:
    """Given /sys/class/video4linux/videoN, walk up through the USB device
    tree looking for idVendor/idProduct files. Returns (vid, pid) or None.
    """
    device_link = video_sysfs_entry / "device"
    if not device_link.exists():
        return None
    try:
        current = device_link.resolve()
    except OSError:
        return None
    for _ in range(8):  # bounded walk up the USB topology
        vid_file = current / "idVendor"
        pid_file = current / "idProduct"
        if vid_file.is_file() and pid_file.is_file():
            try:
                return vid_file.read_text().strip(), pid_file.read_text().strip()
            except OSError:
                return None
        if current.parent == current:
            return None
        current = current.parent
    return None


def scan_v4l2_devices(sysfs_root: Path = _DEFAULT_SYSFS_ROOT) -> list[dict]:
    """Return [{"node": "/dev/videoN", "vid": "xxxx", "pid": "yyyy"}, ...]
    sorted by node name. Entries without a resolvable USB VID:PID are skipped.
    """
    if not sysfs_root.exists():
        return []
    results = []
    for entry in sorted(sysfs_root.iterdir()):
        if not entry.name.startswith("video"):
            continue
        vidpid = _read_usb_vidpid(entry)
        if vidpid is None:
            continue
        results.append({
            "node": f"/dev/{entry.name}",
            "vid": vidpid[0],
            "pid": vidpid[1],
        })
    return results


def select_camera_device(
    entries: list[dict],
    hint: str | None,
    allowed_vidpid: tuple[str, str],
    blocked_vidpids: set[tuple[str, str]],
) -> str:
    """Pure selection logic. See module docstring for the safety rule.

    - If `hint` is given: it must resolve to an entry in `entries` and its
      VID:PID must not be in `blocked_vidpids`. Returns the hint.
    - If `hint` is None: returns the first entry matching `allowed_vidpid`.
    - Raises CameraError on any failure.

    Callers that need to exclude metadata-only V4L2 nodes (same VID:PID
    as the capture node, can't do REQBUFS with VIDEO_CAPTURE type) should
    filter `entries` before calling this — see `probe_has_video_capture`.
    """
    if not entries:
        raise CameraError("no video devices found under /sys/class/video4linux")

    by_node = {e["node"]: e for e in entries}

    if hint is not None:
        if hint not in by_node:
            raise CameraError(
                f"configured camera device {hint} not found in sysfs — "
                f"cannot verify it is safe to open. Available: "
                f"{sorted(by_node.keys())}"
            )
        e = by_node[hint]
        if (e["vid"], e["pid"]) in blocked_vidpids:
            raise CameraError(
                f"refusing to open {hint}: USB {e['vid']}:{e['pid']} is in "
                f"blocked VID:PID list (likely a meeting camera). "
                f"Remove camera_device from config.toml to auto-probe."
            )
        return hint

    for e in entries:
        if (e["vid"], e["pid"]) == allowed_vidpid:
            return e["node"]

    vidpids = sorted({(e["vid"], e["pid"]) for e in entries})
    raise CameraError(
        f"no allowed camera found: want VID:PID {allowed_vidpid[0]}:"
        f"{allowed_vidpid[1]}, saw {vidpids}"
    )


# V4L2_CAP_VIDEO_CAPTURE flag in the device_caps field returned by QUERYCAP.
# Used by probe_has_video_capture() to skip metadata-only nodes that share
# a VID:PID with the real capture node (e.g. uvcvideo exposes an Alcor
# 058f:5608 camera as a capture node plus a separate metadata node; the
# sysfs scanner can't tell them apart and the ordering is not stable).
_V4L2_CAP_VIDEO_CAPTURE = 0x00000001
_VIDIOC_QUERYCAP_CMD = 0x80685600  # __IOR('V', 0, struct v4l2_capability) — 104 bytes


def probe_has_video_capture(node: str) -> bool:
    """Return True iff `node` exposes V4L2_CAP_VIDEO_CAPTURE via QUERYCAP.

    Opens the device briefly and runs VIDIOC_QUERYCAP. Any OS error is
    treated as "not a capture device" — safer to exclude than to guess.
    """
    try:
        fd = os.open(node, os.O_RDWR)
    except OSError:
        return False
    try:
        buf = bytearray(104)
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP_CMD, buf)
        device_caps = int.from_bytes(buf[88:92], "little")
        return bool(device_caps & _V4L2_CAP_VIDEO_CAPTURE)
    except OSError:
        return False
    finally:
        os.close(fd)


def resolve_camera_device(
    hint: str | None,
    sysfs_root: Path = _DEFAULT_SYSFS_ROOT,
    capture_check=None,
) -> str:
    """Scan sysfs and return a safe /dev/videoN path, or raise CameraError.

    If `capture_check` is a callable (typically `probe_has_video_capture`),
    nodes for which it returns False are filtered out before VID:PID
    selection. This is what keeps the resolver from handing a metadata-only
    v4l2 node to `open_camera`. Tests pass `capture_check=None` so they
    can drive selection logic without touching real hardware.
    """
    entries = scan_v4l2_devices(sysfs_root)
    if capture_check is not None:
        entries = [e for e in entries if capture_check(e["node"])]
    return select_camera_device(
        entries, hint, ALCOR_AMBIENT_VIDPID, BLOCKED_VIDPIDS
    )


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
VIDIOC_S_CTRL = 0xC008561C

# V4L2 pixel formats
V4L2_PIX_FMT_YUYV = 0x56595559

# V4L2 buffer types and memory types
V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_MEMORY_MMAP = 1

# V4L2 control IDs
V4L2_CID_BRIGHTNESS = 0x00980900

# Buffer count for mmap
NUM_BUFFERS = 2


class v4l2_format(ctypes.Structure):
    """v4l2_format for video capture (64-bit kernel ABI).

    Kernel layout: `__u32 type` followed by a union `fmt`. On 64-bit the
    union has 8-byte alignment (because variants like v4l2_window contain
    pointer members), so there are 4 bytes of padding between `type` and
    the start of the union. Forgetting that pad shifts every `fmt.pix.*`
    field by 4 bytes and makes S_FMT/G_FMT return garbage.

    Total size must be 208 bytes and is asserted at module load.
    """
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("_type_pad", ctypes.c_uint32),  # 64-bit union alignment
        ("fmt_pix_width", ctypes.c_uint32),
        ("fmt_pix_height", ctypes.c_uint32),
        ("fmt_pix_pixelformat", ctypes.c_uint32),
        ("fmt_pix_field", ctypes.c_uint32),
        ("fmt_pix_bytesperline", ctypes.c_uint32),
        ("fmt_pix_sizeimage", ctypes.c_uint32),
        ("fmt_pix_colorspace", ctypes.c_uint32),
        ("fmt_pix_priv", ctypes.c_uint32),
        ("fmt_pix_flags", ctypes.c_uint32),
        ("_padding", ctypes.c_uint8 * 164),
    ]


class v4l2_control(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("value", ctypes.c_int32),
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
    """v4l2_buffer (64-bit kernel ABI).

    The kernel `m` field is a union `{ __u32 offset; unsigned long userptr;
    void *planes; __s32 fd; }` — 8 bytes wide on 64-bit because of the
    pointer/long variants. The MMAP path only uses the low 4 bytes as
    `offset`, but the struct layout still has to reserve the full 8 bytes,
    otherwise every field after `m` reads 4 bytes early and the ioctl
    overflows the Python buffer by 8 bytes.

    Total size must be 88 bytes and is asserted at module load.
    """
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("bytesused", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        # ctypes adds 4 bytes of padding here for c_long alignment
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
        ("_m_pad", ctypes.c_uint32),  # upper half of 8-byte `m` union
        ("length", ctypes.c_uint32),
        ("reserved2", ctypes.c_uint32),
        ("request_fd", ctypes.c_int32),
        # ctypes adds 4 bytes of tail padding to round to 8-byte alignment
    ]


assert ctypes.sizeof(v4l2_buffer) == 88, (
    f"v4l2_buffer ctypes layout is {ctypes.sizeof(v4l2_buffer)} bytes but "
    f"the 64-bit kernel ABI expects 88. Struct definition is wrong and "
    f"will cause silent ioctl buffer overruns."
)
assert ctypes.sizeof(v4l2_format) == 208, (
    f"v4l2_format ctypes layout is {ctypes.sizeof(v4l2_format)} bytes but "
    f"the 64-bit kernel ABI expects 208. Struct definition is wrong."
)


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


def open_camera(
    device: str,
    width: int = 160,
    height: int = 120,
    brightness: int | None = None,
) -> CameraHandle:
    """Open V4L2 camera device and set up YUYV capture with mmap buffers.

    Re-runs the safety resolver with `device` as a hint — even if a caller
    hands us a concrete path, we still verify it is not in BLOCKED_VIDPIDS
    and that it advertises V4L2_CAP_VIDEO_CAPTURE.

    `brightness`, if not None, is applied as V4L2_CID_BRIGHTNESS after format
    negotiation. Some minimal UVC sensors (e.g. the Alcor 058f:5608 ambient
    module on bambam) expose no gain/exposure controls and default to a
    brightness value that produces near-black frames; a non-None setting
    here is how we shift the operating point into the usable range.
    """
    resolved = resolve_camera_device(device, capture_check=probe_has_video_capture)
    if resolved != device:
        raise CameraError(
            f"refusing to open {device}: resolver chose {resolved}. "
            f"Pass resolve_camera_device(None) result instead."
        )
    fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)

    try:
        # Set format: YUYV 160x120
        fmt = v4l2_format()
        fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        fmt.fmt_pix_width = width
        fmt.fmt_pix_height = height
        fmt.fmt_pix_pixelformat = V4L2_PIX_FMT_YUYV
        _ioctl(fd, VIDIOC_S_FMT, fmt)

        if brightness is not None:
            ctrl = v4l2_control()
            ctrl.id = V4L2_CID_BRIGHTNESS
            ctrl.value = int(brightness)
            try:
                _ioctl(fd, VIDIOC_S_CTRL, ctrl)
            except OSError as e:
                # Sensor doesn't support BRIGHTNESS — not fatal, just means
                # the caller's tuning value can't be applied. Readings will
                # fall back to whatever the hardware default produces.
                raise CameraError(
                    f"failed to set V4L2_CID_BRIGHTNESS={brightness} on "
                    f"{device}: {e}. Set camera_brightness=null in "
                    f"config.toml to skip."
                ) from e

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

    Discards first frame after stream-on (warmup zeros). Raises OSError if
    the camera stops producing frames — a 5-second silence from a 30fps
    sensor means the fd has gone stale (suspend/resume, USB hotplug, driver
    crash) and the caller needs to reopen rather than keep retrying.
    """
    import select

    luminances = []
    frames_captured = 0
    frames_to_capture = num_frames + 1  # +1 for warmup discard

    while frames_captured < frames_to_capture:
        # Wait for frame ready
        ready, _, _ = select.select([handle.fd], [], [], 5.0)
        if not ready:
            raise OSError(
                "capture_luminance: select() timed out waiting for frame — "
                "camera fd is likely stale (suspend/resume or USB hotplug)"
            )

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
