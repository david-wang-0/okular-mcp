"""``okular-mcp-hook``: Claude Code ``UserPromptSubmit`` hook (CONTRACT.md C6-C8).

Reads the hook event JSON on stdin and uses only its ``cwd``. When the active Okular
document lies inside ``cwd``, it prints one JSON object whose ``additionalContext``
says which document and page the user is reading; otherwise it prints nothing. The
system primary selection is added only when its words are found on that document's
current page or a page next to it (C7): otherwise it may come from anywhere, such as
the terminal the prompt is typed in. With SyncTeX data, ``Source:`` lines follow. It
always exits 0, and a watchdog ends it silently if Okular or the bus is slow, so it
never blocks a prompt; the selection has a shorter deadline of its own, so that it
never costs the page sentence.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any

from . import selection, sourcelines, viewer

QDBUS_TIMEOUT = 0.3
"""Seconds one qdbus call may take inside the hook (the server allows ``viewer.TIMEOUT``)."""

WATCHDOG = 0.8
"""Seconds after which the hook gives up and exits 0 without output."""

SELECTION_DEADLINE = 0.6
"""Seconds after the start by which the selection part must be done, else it is left out."""

MIN_SELECTION_TIME = 0.05
"""The selection part is not started with less time than this left."""

MAX_SELECTION = 2000
"""C7: the selected text is cut at this many characters."""


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


def _within(path: str, directory: str) -> bool:
    """Lexically: ``path`` is ``directory`` or lies below it."""
    p, d = os.path.abspath(path), os.path.abspath(directory)
    try:
        return os.path.commonpath([p, d]) == d
    except ValueError:
        return False


def relative_to_cwd(file: str, cwd: str) -> str:
    """C7 ``Source:``: ``file`` relative to ``cwd`` when it lies inside it (lexically or
    after resolving symlinks), else as it is."""
    for f, d in ((file, cwd), (os.path.realpath(file), os.path.realpath(cwd))):
        if _within(f, d):
            rel = os.path.relpath(os.path.abspath(f), os.path.abspath(d))
            return os.path.basename(file) if rel == "." else rel
    return file


def selected(e: dict[str, Any], cwd: str, deadline: float) -> str:
    """C7: the ``Selected text`` and ``Source:`` lines for the active entry ``e``.

    "" when nothing is selected, when the selected words are not on the entry's
    current page or a page next to it, when anything fails, and when ``deadline`` (a
    ``time.monotonic()`` value) comes first.
    """
    def left() -> float:
        return deadline - time.monotonic()
    try:
        if left() < MIN_SELECTION_TIME:
            return ""
        text = selection.read("primary", timeout=left())
        if not text.strip() or left() <= 0:
            return ""
        found = sourcelines.locate(text, e["path"], e["page"], e["pages"], timeout=left())
        if not found or left() <= 0:
            return ""
        tex = sourcelines.tex_lines(e["path"], found, timeout=left()) if left() > 0 else None
    except Exception:  # no selection tool, a timeout, anything: the page sentence stands
        return ""
    if len(text) > MAX_SELECTION:
        text = text[:MAX_SELECTION] + " […]"
    out = f"\nSelected text (page {found.page}):\n{text}"
    for t in tex or []:
        first, last = t["lines"]
        out += f"\nSource: {relative_to_cwd(t['file'], cwd)}:{first}" + (f"-{last}" if last != first else "")
    return out


def context(event: Any, deadline: float | None = None) -> str | None:
    """C7/C8: the ``additionalContext`` text for ``event``, or None to print nothing.

    ``deadline`` (a ``time.monotonic()`` value) bounds the selection part; None leaves
    the selection out.
    """
    cwd = event.get("cwd") if isinstance(event, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return None
    try:
        e = viewer.active()
    except viewer.ViewerError:
        return None
    if not inside(e["path"], cwd):
        return None
    text = f"The user is reading {e['path']} in Okular, page {e['page']} of {e['pages']}."
    return text if deadline is None else text + selected(e, cwd, deadline)


def _output(watchdog: _Watchdog, start: float) -> str | None:
    saved = viewer.TIMEOUT
    viewer.TIMEOUT = QDBUS_TIMEOUT
    try:
        text = context(json.loads(sys.stdin.read()), start + SELECTION_DEADLINE)
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
        start = time.monotonic()
        watchdog = _Watchdog(WATCHDOG)
        out = _output(watchdog, start)
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
