"""Cinnamon ScreenSaver ActiveChanged watcher.

Consumes `dbus-monitor --session` stdout and invokes a callback on every
ScreenSaver.ActiveChanged transition. The watcher is resilient: if
dbus-monitor exits for any reason, it reconnects with exponential backoff.

Design notes
------------

dbus-monitor writes events as a header line + indented arg lines
terminated by a blank line. We flatten each event into a single string
(`header :: arg1 :: arg2 ...`) before parsing, so that
`parse_active_changed` is a trivial pure function that operates on one
flat line. The flattening logic lives in `ScreensaverWatcher.run()`.

We do NOT pipe through mawk/stdbuf here (as in the bash logger): asyncio
reads dbus-monitor's pipe directly via `StreamReader.readline()`, which
handles line buffering natively on the Python side.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


def parse_active_changed(flat_event: str) -> bool | None:
    """Return True/False if this flat event is an ActiveChanged signal.

    Accepts one flattened line of the form
    ``<header> :: <arg1> :: <arg2> ...`` and returns:
      - True  if the header names the ActiveChanged signal and the args
              contain `boolean true`
      - False for the `boolean false` variant
      - None  for anything else (other members, method calls, method
              returns, malformed input, blank lines)

    The parser deliberately requires BOTH `member=ActiveChanged` AND a
    `boolean` arg; this rules out method-return lines that happen to
    carry a boolean (e.g. GetActive) and ActiveChanged headers that
    somehow lost their arg.
    """
    if not flat_event:
        return None
    if "member=ActiveChanged" not in flat_event:
        return None
    # Guard against method returns/calls that also include boolean args.
    if not flat_event.lstrip().startswith("signal"):
        return None
    if "boolean true" in flat_event:
        return True
    if "boolean false" in flat_event:
        return False
    return None


class ScreensaverWatcher:
    """Async dbus-monitor watcher for org.cinnamon.ScreenSaver.ActiveChanged.

    Spawns `dbus-monitor --session "<filter>"`, reads stdout line by
    line, flattens each event block, and awaits the caller-supplied
    coroutine on every state transition. Restarts the subprocess with
    exponential backoff if it exits.
    """

    _FILTER = (
        "type='signal',interface='org.cinnamon.ScreenSaver',"
        "member='ActiveChanged'"
    )

    def __init__(self, on_active: Callable[[bool], Awaitable[None]]):
        self._on_active = on_active
        self._backoff_initial = 1.0
        self._backoff_max = 30.0

    async def run(self) -> None:
        backoff = self._backoff_initial
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "dbus-monitor", "--session", self._FILTER,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except (OSError, FileNotFoundError):
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)
                continue

            try:
                await self._read_events(proc)
            finally:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await proc.wait()
                except Exception:
                    pass

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._backoff_max)

    async def _read_events(self, proc) -> None:
        """Read stdout, flatten header + arg lines, dispatch callbacks.

        dbus-monitor does NOT emit blank-line separators between events,
        despite what you might expect from its formatted output — events
        stream back-to-back. We therefore dispatch on two triggers:

          1. a new `signal`/`method call`/etc header arrives (the
             previous event is complete), OR
          2. we just appended an arg line that makes the buffered event
             parseable (for our filter: a ``boolean`` arg completing an
             ActiveChanged signal).

        Without trigger (2), a one-off `ActiveChanged` would sit in the
        buffer until the next unrelated signal arrived — which on a
        quiet session can be minutes or never.
        """
        stdout = proc.stdout
        header: str | None = None
        args: list[str] = []
        while True:
            raw = await stdout.readline()
            if not raw:  # EOF → subprocess exited
                if header is not None:
                    await self._dispatch(header, args)
                return
            line = raw.decode("utf-8", errors="replace").rstrip("\n")

            if line == "":
                if header is not None:
                    await self._dispatch(header, args)
                header = None
                args = []
                continue

            if line.startswith((" ", "\t")):
                arg = line.strip()
                args.append(arg)
                if header is not None and arg.startswith("boolean "):
                    await self._dispatch(header, args)
                    header = None
                    args = []
            else:
                if header is not None:
                    await self._dispatch(header, args)
                header = line
                args = []

    async def _dispatch(self, header: str, args: list[str]) -> None:
        flat = header
        if args:
            flat = f"{header} :: " + " :: ".join(args)
        result = parse_active_changed(flat)
        if result is not None:
            await self._on_active(result)
