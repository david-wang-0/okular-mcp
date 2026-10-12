"""``okular-mcp-hook``: Claude Code ``UserPromptSubmit`` hook (CONTRACT.md C6-C8).

Reads the hook event JSON on stdin and uses only its ``cwd``. When the active Okular
document lies inside ``cwd``, it prints one JSON object whose ``additionalContext``
says which document and page the user is reading; otherwise it prints nothing. It
never reports the selection: the system primary selection cannot be tied to Okular
(it may come from the terminal the prompt is typed in). It always exits 0, and a
watchdog ends it silently if Okular or the bus is slow, so it never blocks a prompt.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from typing import Any

from . import viewer

QDBUS_TIMEOUT = 0.3
"""Seconds one qdbus call may take inside the hook (the server allows ``viewer.TIMEOUT``)."""

WATCHDOG = 0.8
"""Seconds after which the hook gives up and exits 0 without output."""


class _Timeout(BaseException):
    """Raised by the watchdog; a BaseException so no ``except Exception`` swallows it."""


def _alarm(signum: int, frame: Any) -> None:
    raise _Timeout


class _Watchdog:
    """SIGALRM after ``seconds``; a no-op where SIGALRM is unavailable (not the main thread)."""

    def __init__(self, seconds: float) -> None:
        self.armed, self.previous = False, None
        try:
            self.previous = signal.signal(signal.SIGALRM, _alarm)
            self.armed = True
            signal.setitimer(signal.ITIMER_REAL, seconds)
        except (AttributeError, ValueError, OSError):
            self.cancel()

    def cancel(self) -> None:
        if self.armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous)
            self.armed = False


def inside(path: str, directory: str) -> bool:
    """``path`` is ``directory`` or lies below it, after resolving symlinks (/a/bc is not in /a/b)."""
    p, d = os.path.realpath(path), os.path.realpath(directory)
    try:
        return os.path.commonpath([p, d]) == d
    except ValueError:
        return False


def context(event: Any) -> str | None:
    """C7/C8: the ``additionalContext`` text for ``event``, or None to print nothing."""
    cwd = event.get("cwd") if isinstance(event, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return None
    try:
        e = viewer.active()
    except viewer.ViewerError:
        return None
    if not inside(e["path"], cwd):
        return None
    return f"The user is reading {e['path']} in Okular, page {e['page']} of {e['pages']}."


def _output(watchdog: _Watchdog) -> str | None:
    saved = viewer.TIMEOUT
    viewer.TIMEOUT = QDBUS_TIMEOUT
    try:
        text = context(json.loads(sys.stdin.read()))
    finally:
        viewer.TIMEOUT = saved
        watchdog.cancel()
    if not text:
        return None
    return json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                              "additionalContext": text}})


def main() -> None:
    """Print one JSON object or nothing; exit 0 whatever happens (C6)."""
    watchdog = None
    try:
        watchdog = _Watchdog(WATCHDOG)
        out = _output(watchdog)
    except BaseException:  # bad JSON, viewer failure, watchdog, anything: never block a prompt
        out = None
    try:
        if watchdog is not None:
            watchdog.cancel()  # already cancelled unless the alarm fired inside cancel()
        if out:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
    except BaseException:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
