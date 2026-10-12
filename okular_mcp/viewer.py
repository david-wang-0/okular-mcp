"""Okular over the session D-Bus: which documents are open, on what page, and goto.

It talks to Okular through the ``qdbus`` CLI (part of Qt, always present next to
Okular), so there is no Python D-Bus dependency. Every Okular process registers a
single ``/okular`` object, so one process is one window here (tabs other than the
first-opened one are unreachable over D-Bus).

The shapes follow CONTRACT.md: an *entry* is a dict ``{"viewer", "window", "path",
"page", "pages", "active"}``; the active entry is the first window with a document.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from .env import ensure_session_env

NO_DOCUMENT = "no Okular window with a PDF open"
"""Prefix of the C1 error: no window has a document, or Okular is not running."""

NOT_RUNNING = f"{NO_DOCUMENT} (Okular is not running on the session bus)"

TIMEOUT = 5.0
"""Seconds one qdbus call may take; the prompt hook lowers it."""

IFACE = "org.kde.okular"


class ViewerError(RuntimeError):
    """No viewer is running, nothing is open, or a D-Bus call fails."""


@dataclass
class Window:
    """One Okular process; ``path`` is ``""`` when it has no document open."""

    service: str
    pid: int
    path: str
    page: int
    pages: int

    def entry(self, active: bool) -> dict[str, Any]:
        return {"viewer": "okular", "window": self.service, "path": self.path,
                "page": self.page, "pages": self.pages, "active": active}


def _qdbus() -> str:
    ensure_session_env()
    for name in ("qdbus", "qdbus6", "qdbus-qt6", "qdbus-qt5"):
        exe = shutil.which(name)
        if exe:
            return exe
    raise ViewerError("qdbus not found on PATH (install qt6-tools / qt5-tools)")


def _call(*args: str) -> str:
    try:
        out = subprocess.run([_qdbus(), *args], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise ViewerError(f"qdbus timed out: {' '.join(args)}") from e
    if out.returncode != 0:
        raise ViewerError(f"qdbus {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    # qdbus prints each service with a leading space; strip everything.
    return out.stdout.strip()


def _okular(service: str, method: str, *args: str) -> str:
    return _call(service, "/okular", f"{IFACE}.{method}", *args)


def _services() -> list[str]:
    names = [line.strip() for line in _call().splitlines()]
    return [n for n in names if n.startswith("org.kde.okular")]


def _pid(service: str) -> int:
    return int(_call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                     "org.freedesktop.DBus.GetConnectionUnixProcessID", service))


def _int(s: str) -> int:
    return int(s or 0)


def windows() -> list[Window]:
    """One per running Okular process (each registers two D-Bus names), in bus order.

    Includes windows without a document; ``[]`` when Okular is not running.
    """
    seen: dict[int, Window] = {}
    for svc in _services():
        try:
            pid = _pid(svc)
        except (ViewerError, ValueError):
            continue
        if pid in seen:
            continue
        try:
            seen[pid] = Window(svc, pid, _okular(svc, "currentDocument"),
                               _int(_okular(svc, "currentPage")), _int(_okular(svc, "pages")))
        except (ViewerError, ValueError):
            continue
    return list(seen.values())


def _windows_or_c1() -> list[Window]:
    """``windows()``, with a bus failure reported under the C1 message."""
    try:
        return windows()
    except ViewerError as e:
        raise ViewerError(f"{NO_DOCUMENT} ({e})") from e


def _open_windows() -> list[Window]:
    """Windows with a document, the active one first; the C1 error when there are none."""
    ws = _windows_or_c1()
    if not ws:
        raise ViewerError(NOT_RUNNING)
    ws = [w for w in ws if w.path]
    if not ws:
        raise ViewerError(NO_DOCUMENT)
    return ws


def entries() -> list[dict[str, Any]]:
    """C1: one entry per window with a document, the active (first) one first."""
    return [w.entry(i == 0) for i, w in enumerate(_open_windows())]


def active() -> dict[str, Any]:
    """The active entry, or the C1 error."""
    return _open_windows()[0].entry(True)


def _same(a: str, b: str) -> bool:
    return a == b or os.path.realpath(a) == os.path.realpath(b)


def goto(page: int, path: str | None = None) -> dict[str, Any]:
    """C3: show ``page`` (1-based) of ``path`` (default: the active entry's document).

    Opens ``path`` in the first Okular window if no window shows it (``cannot open``
    if Okular fails to load it), checks ``page`` against that document's page count,
    then moves; returns the document's entry.
    """
    if path is None:
        w = _open_windows()[0]
    else:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no such file: {path}")
        path = os.path.abspath(path)
        ws = _windows_or_c1()
        if not ws:
            raise ViewerError(NOT_RUNNING)
        w = next((w for w in ws if w.path and _same(w.path, path)), None)
        if w is None:
            w = ws[0]
            _okular(w.service, "openDocument", path)
            # openDocument returns nothing: a load failure shows as another (or no) document.
            shown = _okular(w.service, "currentDocument")
            if not shown or not _same(shown, path) or _int(_okular(w.service, "pages")) < 1:
                raise ViewerError(f"cannot open {path}")
    pages = _int(_okular(w.service, "pages"))
    if not 1 <= page <= pages:
        raise ValueError(f"page {page} outside 1-{pages}")
    _okular(w.service, "goToPage", str(page))
    for e in entries():
        if e["window"] == w.service:
            return {**e, "page": page}
    raise ViewerError(f"Okular window {w.service} closed while moving to page {page}")
