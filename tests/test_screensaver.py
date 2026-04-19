"""Tests for screensaver.py — dbus-monitor parser + async watcher.

The parser consumes *flattened* dbus-monitor event lines (header joined
with its arg lines by ` :: `) and returns the ScreenSaver.ActiveChanged
state, or None for unrelated/malformed input.
"""

import asyncio

import pytest

from screensaver import parse_active_changed


class TestParseActiveChanged:
    """Pure parser: flat line → True/False/None."""

    def test_active_changed_true(self):
        line = (
            "signal time=1.0 sender=:1.42 -> destination=(null destination) "
            "serial=9 path=/org/cinnamon/ScreenSaver; "
            "interface=org.cinnamon.ScreenSaver; member=ActiveChanged "
            ":: boolean true"
        )
        assert parse_active_changed(line) is True

    def test_active_changed_false(self):
        line = (
            "signal time=1.0 sender=:1.42 -> destination=(null destination) "
            "serial=10 path=/org/cinnamon/ScreenSaver; "
            "interface=org.cinnamon.ScreenSaver; member=ActiveChanged "
            ":: boolean false"
        )
        assert parse_active_changed(line) is False

    def test_ignores_other_member(self):
        line = (
            "signal time=1.0 sender=:1.42 "
            "path=/org/cinnamon/ScreenSaver; "
            "interface=org.cinnamon.ScreenSaver; member=NameAcquired "
            ":: string \":1.42\""
        )
        assert parse_active_changed(line) is None

    def test_ignores_inhibit_method_call(self):
        line = (
            "method call time=1.0 sender=:1.42 "
            "path=/org/freedesktop/ScreenSaver; "
            "interface=org.freedesktop.ScreenSaver; member=Inhibit "
            ":: string \"app\" :: string \"reason\""
        )
        assert parse_active_changed(line) is None

    def test_ignores_active_changed_without_boolean(self):
        """ActiveChanged should always carry a boolean arg, but guard anyway."""
        line = (
            "signal path=/org/cinnamon/ScreenSaver; "
            "interface=org.cinnamon.ScreenSaver; member=ActiveChanged"
        )
        assert parse_active_changed(line) is None

    def test_ignores_empty_line(self):
        assert parse_active_changed("") is None

    def test_ignores_garbage(self):
        assert parse_active_changed("not a dbus line at all") is None

    def test_ignores_getactive_method_return(self):
        """GetActive is a method with a boolean return — must not match."""
        line = (
            "method return time=1.0 sender=:1.42 "
            "-> destination=:1.99 serial=77 reply_serial=3 "
            ":: boolean true"
        )
        assert parse_active_changed(line) is None


class TestScreensaverWatcher:
    """Async watcher reads dbus-monitor stdout and dispatches callbacks."""

    @pytest.mark.asyncio
    async def test_invokes_callback_on_state_changes(self, monkeypatch):
        """Two scripted transitions → callback awaited with [True, False].

        Uses the real dbus-monitor output shape: events are streamed
        back-to-back with NO blank-line separator. An earlier version
        of this test scripted blank lines, which masked a real-world
        bug where a one-off ActiveChanged sat un-dispatched because no
        follow-up signal arrived to trigger the "new header" flush.
        """
        from screensaver import ScreensaverWatcher

        scripted = [
            b"signal path=/org/cinnamon/ScreenSaver; "
            b"interface=org.cinnamon.ScreenSaver; member=ActiveChanged\n",
            b"   boolean true\n",
            b"signal path=/org/cinnamon/ScreenSaver; "
            b"interface=org.cinnamon.ScreenSaver; member=ActiveChanged\n",
            b"   boolean false\n",
        ]

        class FakeStream:
            def __init__(self, lines):
                self._lines = list(lines)

            async def readline(self):
                if self._lines:
                    return self._lines.pop(0)
                return b""  # EOF

        class FakeProc:
            def __init__(self):
                self.stdout = FakeStream(scripted)
                self.returncode = 0
                self._killed = False

            def kill(self):
                self._killed = True

            async def wait(self):
                return 0

        created = []

        async def fake_create(*args, **kwargs):
            p = FakeProc()
            created.append(p)
            return p

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", fake_create,
        )

        received: list[bool] = []

        async def on_active(active: bool) -> None:
            received.append(active)

        watcher = ScreensaverWatcher(on_active=on_active)
        # Shrink backoff so EOF-restart doesn't stall the test.
        watcher._backoff_initial = 0.01
        watcher._backoff_max = 0.01

        task = asyncio.create_task(watcher.run())
        # Wait until both transitions are delivered, then cancel.
        for _ in range(100):
            if len(received) >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert received == [True, False]

    @pytest.mark.asyncio
    async def test_restarts_on_subprocess_exit(self, monkeypatch):
        """If dbus-monitor exits early, watcher reconnects."""
        from screensaver import ScreensaverWatcher

        call_count = {"n": 0}

        class FakeStream:
            def __init__(self, lines):
                self._lines = list(lines)

            async def readline(self):
                if self._lines:
                    return self._lines.pop(0)
                return b""

        class FakeProc:
            def __init__(self, lines):
                self.stdout = FakeStream(lines)
                self.returncode = 0

            def kill(self):
                pass

            async def wait(self):
                return 0

        async def fake_create(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeProc([])  # immediate EOF
            return FakeProc([
                b"signal path=/org/cinnamon/ScreenSaver; "
                b"interface=org.cinnamon.ScreenSaver; member=ActiveChanged\n",
                b"   boolean true\n",
            ])

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", fake_create,
        )

        received: list[bool] = []

        async def on_active(active: bool) -> None:
            received.append(active)

        watcher = ScreensaverWatcher(on_active=on_active)
        watcher._backoff_initial = 0.01
        watcher._backoff_max = 0.01

        task = asyncio.create_task(watcher.run())
        for _ in range(200):
            if received:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert received == [True]
        assert call_count["n"] >= 2  # at least one restart

    @pytest.mark.asyncio
    async def test_dispatches_single_event_without_trailing_flush(self, monkeypatch):
        """A lone ActiveChanged with no follow-up signal must dispatch.

        Regression: on a real session, `cinnamon-screensaver-command
        --lock` emits one ActiveChanged and then the session goes
        quiet. Previously the watcher only dispatched the buffered
        event when the NEXT header arrived, so the lone event sat
        un-dispatched indefinitely and the backlights never dropped.
        """
        from screensaver import ScreensaverWatcher

        scripted = [
            b"signal path=/org/cinnamon/ScreenSaver; "
            b"interface=org.cinnamon.ScreenSaver; member=ActiveChanged\n",
            b"   boolean true\n",
        ]

        class FakeStream:
            def __init__(self, lines):
                self._lines = list(lines)
                self._pending_eof_awaits = 0

            async def readline(self):
                if self._lines:
                    return self._lines.pop(0)
                # After scripted input is exhausted, simulate "quiet
                # pipe" for a few awaits, THEN return EOF. Without this
                # the watcher would get EOF immediately and could mask
                # the bug via the final-flush path.
                self._pending_eof_awaits += 1
                if self._pending_eof_awaits < 20:
                    await asyncio.sleep(0.005)
                    return b""  # still treated as EOF once delivered
                return b""

        class FakeProc:
            def __init__(self):
                self.stdout = FakeStream(scripted)
                self.returncode = 0

            def kill(self):
                pass

            async def wait(self):
                return 0

        async def fake_create(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)

        received: list[bool] = []

        async def on_active(active: bool) -> None:
            received.append(active)

        watcher = ScreensaverWatcher(on_active=on_active)
        watcher._backoff_initial = 0.01
        watcher._backoff_max = 0.01

        task = asyncio.create_task(watcher.run())
        # Give the watcher plenty of time; without the fix, nothing
        # will land in `received` because no follow-up header arrives.
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert received == [True], (
            "lone ActiveChanged must dispatch without waiting for a "
            "trailing blank line or next header"
        )
