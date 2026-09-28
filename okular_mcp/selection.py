"""Text selection and clipboard, read straight from the display server.

The primary selection is whatever text is currently highlighted with the mouse in
any window, so the LLM can see what the reader is pointing at without a copy.
"""

from __future__ import annotations

import os
import shutil
import subprocess


class SelectionError(RuntimeError):
    pass


def _run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired as e:
        raise SelectionError(f"{cmd[0]} timed out") from e
    if out.returncode != 0:
        err = out.stderr.strip()
        if "Nothing is copied" in err or "no selection" in err.lower():
            return ""
        raise SelectionError(f"{' '.join(cmd)} failed: {err}")
    return out.stdout


def read(source: str = "primary") -> str:
    """``source`` is ``primary`` (mouse selection) or ``clipboard``."""
    if source not in ("primary", "clipboard"):
        raise SelectionError("source must be 'primary' or 'clipboard'")
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        args = ["wl-paste", "--no-newline"]
        if source == "primary":
            args.insert(1, "--primary")
        return _run(args)
    if shutil.which("xclip"):
        return _run(["xclip", "-o", "-selection", source])
    if shutil.which("xsel"):
        return _run(["xsel", "-o", "--primary" if source == "primary" else "--clipboard"])
    raise SelectionError("no clipboard tool found: install wl-clipboard (Wayland) or xclip/xsel (X11)")
