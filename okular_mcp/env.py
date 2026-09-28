"""Recover the session-bus and Wayland variables when the MCP client dropped them.

MCP clients commonly start stdio servers with a minimal environment (Codex keeps
only HOME, PATH, SHELL and USER; the Python SDK's client is similar), which loses
DBUS_SESSION_BUS_ADDRESS and WAYLAND_DISPLAY. Both have well-known defaults under
XDG_RUNTIME_DIR on a systemd desktop, so fill them in from there.
"""

from __future__ import annotations

import os


def runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def ensure_session_env() -> None:
    rt = runtime_dir()
    os.environ.setdefault("XDG_RUNTIME_DIR", rt)
    if "DBUS_SESSION_BUS_ADDRESS" not in os.environ and os.path.exists(f"{rt}/bus"):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
    if "WAYLAND_DISPLAY" not in os.environ:
        for name in ("wayland-0", "wayland-1"):
            if os.path.exists(f"{rt}/{name}"):
                os.environ["WAYLAND_DISPLAY"] = name
                break
