"""Viewer backend: what document is open, on what page, and driving the viewer.

Only the Okular backend exists today. It talks to Okular over the session D-Bus
through the ``qdbus`` CLI (part of Qt, always present next to Okular), so there
is no Python D-Bus dependency. A zathura or web backend would implement the
same three functions.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


class ViewerError(RuntimeError):
    """Raised when no viewer is running or a D-Bus call fails."""


@dataclass
class ViewerWindow:
    service: str
    pid: int
    path: str
    page: int
    pages: int
    viewer: str = "okular"


def _qdbus() -> str:
    for name in ("qdbus", "qdbus6", "qdbus-qt6", "qdbus-qt5"):
        exe = shutil.which(name)
        if exe:
            return exe
    raise ViewerError("qdbus not found on PATH (install qt6-tools / qt5-tools)")


def _call(*args: str) -> str:
    try:
        out = subprocess.run([_qdbus(), *args], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired as e:
        raise ViewerError(f"qdbus timed out: {' '.join(args)}") from e
    if out.returncode != 0:
        raise ViewerError(f"qdbus {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    # qdbus prints each service with a leading space; strip everything.
    return out.stdout.strip()


def _services() -> list[str]:
    names = [line.strip() for line in _call().splitlines()]
    return [n for n in names if n.startswith("org.kde.okular")]


def _pid(service: str) -> int:
    return int(_call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                     "org.freedesktop.DBus.GetConnectionUnixProcessID", service))


def windows() -> list[ViewerWindow]:
    """One entry per running Okular process (each registers two D-Bus names)."""
    seen: dict[int, ViewerWindow] = {}
    for svc in _services():
        try:
            pid = _pid(svc)
        except ViewerError:
            continue
        if pid in seen:
            continue
        try:
            path = _call(svc, "/okular", "org.kde.okular.currentDocument")
            page = int(_call(svc, "/okular", "org.kde.okular.currentPage") or 0)
            pages = int(_call(svc, "/okular", "org.kde.okular.pages") or 0)
        except (ViewerError, ValueError):
            continue
        seen[pid] = ViewerWindow(service=svc, pid=pid, path=path, page=page, pages=pages)
    if not seen:
        raise ViewerError("no Okular window found on the session bus (is Okular running?)")
    return list(seen.values())


def current() -> ViewerWindow:
    """The window to use when the caller gives no path: the first with a document."""
    ws = windows()
    for w in ws:
        if w.path:
            return w
    raise ViewerError("Okular is running but has no document open")


def find(path: str) -> ViewerWindow | None:
    for w in windows():
        if w.path == path:
            return w
    return None


def goto(page: int, path: str | None = None) -> ViewerWindow:
    """Show ``page`` (1-based) of ``path`` (default: current document)."""
    if path:
        w = find(path)
        if w is None:
            w = windows()[0]
            _call(w.service, "/okular", "org.kde.okular.openDocument", path)
    else:
        w = current()
    _call(w.service, "/okular", "org.kde.okular.goToPage", str(page))
    return (find(path) if path else None) or current()


def reload(path: str) -> None:
    """Ask the window showing ``path`` to reload it from disk (no-op if none)."""
    w = find(path)
    if w is not None:
        _call(w.service, "/okular", "org.kde.okular.reload")
