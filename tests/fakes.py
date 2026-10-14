"""Fake ``qdbus``, ``wl-paste``, ``xclip``, ``pdftotext`` and ``synctex`` executables.

For the tests that run the real entry points as processes (the MCP server over
stdio, the prompt hook, and the integration suite's fake selection). ``FakeBin``
writes one small script per tool into a directory to put on ``PATH``; each runs
``main`` here with the tool's name. They share a JSON state file the test edits
between calls, and append every call (argv and the session variables it saw) to a
JSON-lines log.

The state (all optional):

- ``procs``: pid (as a string) -> ``{"path", "page", "pages"}``; ``names``: D-Bus name
  -> pid (None: the pid lookup fails). ``docs``: path -> page count for openDocument
  (default 9; None: the load fails and the window is left empty; "dialog": a broken
  PDF, shown with no pages, and openDocument hangs as behind Okular's error dialog).
  The fake ``qdbus`` moves pages and opens documents in the state, as Okular would.
- ``primary``, ``clipboard``: the selections (None: "Nothing is copied", exit 1).
- ``layer``: page number (as a string) -> printed lines, the text ``pdftotext
  -bbox-layout`` reports (words on the ``box`` grid).
- ``edit``: ``[page, line i, word j, input, line]`` records for ``synctex edit``;
  ``views``: ``[line, file, page, x, y]`` records for ``synctex view``.
- ``slow``: tool -> seconds to sleep first; ``fail``: tool -> stderr text, exit 1;
  ``garbage``: tool -> stdout text printed instead of an answer.

No pytest import: the scripts import this module with the interpreter's ``-S``.
"""

from __future__ import annotations

import json
import os
import sys
import sysconfig
import time
from urllib.parse import unquote, urlsplit

TOOLS = ("qdbus", "wl-paste", "xclip", "pdftotext", "synctex")
SESSION = ("DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")
DBUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.GetConnectionUnixProcessID")

SCRIPT = """#!{python} -S
import sys
sys.path.insert(0, {here!r})
import fakes
fakes.main({name!r}, {state!r}, {log!r}, sys.argv[1:])
"""


ENTRY_POINTS = {"okular-mcp": "okular_mcp.server", "okular-mcp-hook": "okular_mcp.hook",
                "okular-mcp-open": "okular_mcp.open"}


def entry_point(name: str) -> list[str]:
    """The argv that runs the console script ``name``: the installed script of this
    interpreter's environment, else ``python -m`` its module."""
    exe = os.path.join(sysconfig.get_path("scripts"), name)
    if os.access(exe, os.X_OK):
        return [exe]
    return [sys.executable, "-m", ENTRY_POINTS[name]]


# --- the text layer and SyncTeX output, shared with conftest.Tools ---

def box(i: int, j: int) -> tuple[float, float, float, float]:
    """The grid the fake text layer puts word ``j`` of printed line ``i`` on: (x0, y0, x1, y1)."""
    return 50.0 + 60 * j, 100.0 + 20 * i, 90.0 + 60 * j, 110.0 + 20 * i


def xhtml(pages: dict[int, list[str]], numbers) -> str:
    """``pdftotext -bbox-layout`` output for ``numbers`` (pages not in ``pages`` are empty)."""
    out = ['<?xml version="1.0"?>\n<html><body>\n<doc>']
    for n in numbers:
        out.append('  <page width="612.000000" height="792.000000">\n    <flow>\n      <block>')
        for i, line in enumerate(pages.get(n, [])):
            out.append('        <line xMin="0" yMin="0" xMax="0" yMax="0">')
            for j, word in enumerate(line.split()):
                x0, y0, x1, y1 = box(i, j)
                word = word.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                out.append(f'          <word xMin="{x0:f}" yMin="{y0:f}" xMax="{x1:f}" yMax="{y1:f}">{word}</word>')
            out.append("        </line>")
        out.append("      </block>\n    </flow>\n  </page>")
    out.append("</doc>\n</body>\n</html>")
    return "\n".join(out)


def pdftotext(pages: dict[int, list[str]], argv: list[str]) -> str:
    """``pdftotext -f F -l L -bbox-layout <pdf> -``; ``L`` is clamped to the last page."""
    first, last = int(argv[1]), min(int(argv[3]), max(pages, default=0))
    return xhtml(pages, range(first, last + 1))


def synctex_edit(edit: dict[tuple[int, int, int], tuple[str, int]], point: str) -> str:
    """``synctex edit -o <page>:<x>:<y>:<pdf>``: the record of the word box around the point."""
    page, x, y, _ = point.split(":", 3)
    for (p, i, j), (inp, line) in edit.items():
        x0, y0, x1, y1 = box(i, j)
        if p == int(page) and x0 <= float(x) <= x1 and y0 <= float(y) <= y1:
            return ("This is SyncTeX command line utility, version 1.5\nSyncTeX result begin\n"
                    f"Output:/x.pdf\nInput:{inp}\nLine:{line}\nColumn:-1\nOffset:0\nContext:\n"
                    "SyncTeX result end\n")
    return "This is SyncTeX command line utility, version 1.5\n"


def synctex_view(views: dict[tuple[int, str], list[tuple[int, float, float]]], point: str) -> str:
    """``synctex view -i <line>:<column>:<file> -o <pdf>``."""
    line, _, file = point.split(":", 2)
    res = views.get((int(line), file), [])
    if not res:
        return f"SyncTeX Warning: No tag for {file}\n"
    return ("SyncTeX result begin\n" + "".join(
        f"Output:/x.pdf\nPage:{p}\nx:{x}\ny:{y}\nh:0\nv:0\nW:10\nH:10\nbefore:\noffset:-1\nmiddle:\nafter:\n"
        for p, x, y in res) + "SyncTeX result end\n")


# --- the executables ---

class _Exit(Exception):
    def __init__(self, code: int, err: str = "") -> None:
        self.code, self.err = code, err


def _qdbus(state: dict, argv: list[str]) -> str:
    names, procs, docs = state.setdefault("names", {}), state.setdefault("procs", {}), state.get("docs", {})
    if not argv:
        # As qdbus prints ListNames: unique names flush left, well-known names indented.
        return "\n".join([":1.0", "org.freedesktop.DBus", ":1.42", *(f" {n}" for n in names), " org.kde.kwin"])
    if tuple(argv[:3]) == DBUS:
        pid = names.get(argv[3])
        if pid is None:
            raise _Exit(1, f"Error: org.freedesktop.DBus.Error.NameHasNoOwner\nCould not get PID of name '{argv[3]}'")
        return str(pid)
    svc, obj, method, *rest = argv
    if svc not in names or names[svc] is None:
        raise _Exit(1, f"Service '{svc}' does not exist.")
    p = procs[str(names[svc])]
    name = method.rsplit(".", 1)[1]
    if obj != "/okular" or not method.startswith("org.kde.okular."):
        raise _Exit(1, f"Cannot find '{method}' in object {obj} at {svc}")
    if name == "currentDocument":
        return p["path"]
    if name in ("currentPage", "pages"):
        return str(p["page" if name == "currentPage" else "pages"])
    if name == "openDocument":
        url = urlsplit(rest[0])
        path = unquote(url.path)
        state.setdefault("opened", []).append(rest[0])
        n = docs.get(path, 9)
        if n == "dialog":
            p.update(path=path, page=0, pages=0)
            state["_hang"] = True
        elif n:
            p.update(path=path, page=1, pages=n)
        else:
            p.update(path="", page=0, pages=0)
        return ""
    if name == "goToPage":
        p["page"] = int(rest[0])
        return ""
    raise _Exit(1, f"Cannot find '{method}' in object /okular at {svc}")


def _selection(state: dict, which: str) -> str:
    text = state.get(which)
    if text is None:
        raise _Exit(1, "Nothing is copied")
    return text


def _run(name: str, state: dict, argv: list[str]) -> str:
    if name == "qdbus":
        return _qdbus(state, argv)
    if name == "wl-paste":
        return _selection(state, "primary" if "--primary" in argv else "clipboard")
    if name == "xclip":
        return _selection(state, argv[argv.index("-selection") + 1])
    layer = {int(k): v for k, v in state.get("layer", {}).items()}
    if name == "pdftotext":
        return pdftotext(layer, argv)
    if name == "synctex" and argv[0] == "edit":
        return synctex_edit({(p, i, j): (inp, line) for p, i, j, inp, line in state.get("edit", [])}, argv[2])
    if name == "synctex" and argv[0] == "view":
        views: dict = {}
        for line, file, page, x, y in state.get("views", []):
            views.setdefault((line, file), []).append((page, x, y))
        return synctex_view(views, argv[2])
    raise _Exit(2, f"{name}: unexpected arguments {argv}")


def main(name: str, state_file: str, log_file: str, argv: list[str]) -> None:
    with open(state_file) as f:
        state = json.load(f)
    with open(log_file, "a") as f:
        f.write(json.dumps({"tool": name, "argv": argv, "env": {k: os.environ.get(k) for k in SESSION}}) + "\n")
    time.sleep(state.get("slow", {}).get(name, 0))
    if name in state.get("fail", {}):
        sys.stderr.write(state["fail"][name] + "\n")
        sys.exit(1)
    if name in state.get("garbage", {}):
        sys.stdout.write(state["garbage"][name])
        return
    try:
        out = _run(name, state, argv)
    except _Exit as e:
        sys.stderr.write(e.err + "\n")
        sys.exit(e.code)
    hang = state.pop("_hang", False)
    if name == "qdbus":  # it may have opened a document or moved a page
        with open(state_file + ".tmp", "w") as f:
            json.dump(state, f)
        os.replace(state_file + ".tmp", state_file)
    if hang:
        time.sleep(60)  # the caller's timeout kills it
    if out:
        sys.stdout.write(out if name in ("wl-paste", "xclip") else out + "\n")


class FakeBin:
    """The fake tools in ``root/bin`` (``path``), their ``state`` and call ``log``."""

    def __init__(self, root, tools=TOOLS) -> None:
        self.dir = os.path.join(str(root), "bin")
        self.state_file = os.path.join(str(root), "state.json")
        self.log_file = os.path.join(str(root), "calls.jsonl")
        os.makedirs(self.dir, exist_ok=True)
        open(self.log_file, "w").close()
        here = os.path.dirname(os.path.abspath(__file__))
        for name in tools:
            exe = os.path.join(self.dir, name)
            with open(exe, "w") as f:
                f.write(SCRIPT.format(python=sys.executable, here=here, name=name,
                                      state=self.state_file, log=self.log_file))
            os.chmod(exe, 0o755)
        self.state: dict = {}
        self.save()

    @property
    def path(self) -> str:
        return self.dir

    def save(self) -> None:
        with open(self.state_file + ".tmp", "w") as f:
            json.dump(self.state, f)
        os.replace(self.state_file + ".tmp", self.state_file)

    def set(self, **kw) -> None:
        """Replace these state keys and save."""
        self.state.update(kw)
        self.save()

    def reset(self, **kw) -> None:
        """Start from an empty state with these keys, and an empty log."""
        self.state = dict(kw)
        self.save()
        open(self.log_file, "w").close()

    def windows(self, *windows: tuple[int, str, int, int], docs: dict | None = None) -> None:
        """Okular processes ``(pid, path, page, pages)``, each with its two bus names, in order."""
        procs, names = {}, {}
        for pid, path, page, pages in windows:
            procs[str(pid)] = {"path": path, "page": page, "pages": pages}
            names[f"org.kde.okular-{pid}"] = pid
            names[f"org.kde.okular.Instance_{pid:032x}"] = pid
        self.set(procs=procs, names=names, docs=docs or {})

    def current(self) -> dict:
        """The state as the fake qdbus left it."""
        with open(self.state_file) as f:
            return json.load(f)

    def calls(self, tool: str | None = None) -> list[dict]:
        with open(self.log_file) as f:
            calls = [json.loads(line) for line in f if line.strip()]
        return [c for c in calls if tool is None or c["tool"] == tool]
