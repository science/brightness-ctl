"""Shared fixtures for brightness-ctl tests."""

import sys
from pathlib import Path

# Add src/ to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture(autouse=True)
def _stub_ambient_task(monkeypatch):
    """Neutralise the real camera path in every test.

    `Daemon._start_ambient_task` schedules an asyncio task that tries to
    open a real V4L2 device. On a developer machine with a live
    brightness-ctl daemon already running, the test task races the real
    daemon for the camera fd, loses with EBUSY, and — because we now
    surface open_camera errors instead of swallowing them — flips
    `state.autobrightness_enabled = False` before the test assertion
    runs. That produced a once-in-a-while flake in test_daemon.py's
    auto-on tests.

    Stubbing `_start_ambient_task` to a no-op keeps all unit/integration
    tests hardware-free and lets auto-on tests check command-path
    behavior without racing real hardware. Tests that want to exercise
    the ambient loop directly should call `_ambient_light_loop` with a
    mocked camera backend — none currently do.
    """
    try:
        import daemon as daemon_module
    except ImportError:
        return
    monkeypatch.setattr(
        daemon_module.Daemon, "_start_ambient_task", lambda self: None
    )
