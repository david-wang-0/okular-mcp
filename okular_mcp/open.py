"""``okular-mcp-open``: open a PDF in Okular, or do a SyncTeX forward search into it.

    okular-mcp-open <pdf>
    okular-mcp-open --tex <file> --line <n> [--column <n>] <pdf>

The same command line as vscode-pdf-mcp-open, so LaTeX Workshop (or any other tool)
can be pointed at either viewer. A window that already shows the PDF is used as it is;
otherwise a new Okular window opens it. A forward search asks ``synctex view`` for the
page first, then hands Okular the ``#src:<line> <file>`` URL, which it resolves itself
(this opens the document anew). Prints ``<pdf>: page N of M``; exit 0 on success, 1
with the reason on stderr, 2 on bad arguments.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from . import sourcelines, viewer

PROG = "okular-mcp-open"

USAGE = (f"usage: {PROG} <pdf>\n"
         f"       {PROG} --tex <file> --line <n> [--column <n>] <pdf>\n")

HELP = USAGE + """
Open <pdf> in Okular: a window that shows it already is left as it is, else a new
Okular window opens it. With --tex and --line (1-based), a SyncTeX forward search:
Okular shows the place of that source line (the PDF needs a .synctex.gz or .synctex
next to it). --column is accepted for compatibility with vscode-pdf-mcp-open and
ignored: Okular has no column in a forward search. Okular opens the document anew for
a forward search, and asks to save unsaved annotations first.
Prints "<pdf>: page N of M"; exit 0 on success, 1 when Okular cannot do it, 2 on bad
arguments.
"""

OPEN_TIMEOUT = 15.0
"""Seconds for Okular to start and load the document."""

SYNCTEX_TIMEOUT = 10.0


class Failure(RuntimeError):
    """Exit 1 with this message."""


def parse_args(argv: list[str]) -> dict[str, Any]:
    """``{"pdf", "tex"?, "line"?, "column"?}``, ``{"help": True}`` or ``{"error": msg}``."""
    opts: dict[str, str] = {}
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            return {"help": True}
        name = a[2:].split("=", 1)[0] if a.startswith("--") else ""
        if name in ("tex", "line", "column"):
            if "=" in a:
                value = a.split("=", 1)[1]
            elif i + 1 < len(argv):
                i += 1
                value = argv[i]
            else:
                return {"error": f"--{name} needs a value"}
            opts[name] = value
        elif a == "--":
            rest += argv[i + 1:]
            break
        elif a.startswith("--"):
            return {"error": f"unknown option {a}"}
        else:
            rest.append(a)
        i += 1
    if len(rest) != 1:
        return {"error": "give exactly one PDF" if rest else "no PDF given"}
    pdf = os.path.abspath(rest[0])
    if "tex" not in opts:
        if "line" in opts or "column" in opts:
            return {"error": "--line and --column need --tex"}
        return {"pdf": pdf}
    try:
        line = int(opts.get("line", ""))
    except ValueError:
        line = 0
    if line < 1:
        return {"error": "--tex needs --line <n>, a positive integer"}
    out: dict[str, Any] = {"pdf": pdf, "tex": os.path.abspath(opts["tex"]), "line": line}
    if "column" in opts:
        try:
            column = int(opts["column"])
        except ValueError:
            return {"error": "--column must be an integer"}
        if column >= 0:  # LaTeX Workshop and others may pass -1 for "no column"
            out["column"] = column
    return out


def _check(pdf: str) -> None:
    if not os.path.isfile(pdf):
        raise Failure(f"no such file: {pdf}")


def _load(service: str, url: str, pdf: str, page: int | None = None) -> viewer.Window:
    """Open ``url`` in ``service`` and wait for ``pdf`` (on ``page``) to show."""
    if not viewer.open_url(service, url):
        w = viewer.window(service)  # an error dialog about the file, or a slow load
        if not (w.path and os.path.realpath(w.path) == os.path.realpath(pdf) and w.pages >= 1):
            raise Failure(f"cannot open {pdf}")
    return viewer.wait_loaded(service, pdf, page, OPEN_TIMEOUT)


def _into_new_window(url: str, pdf: str, page: int | None = None) -> viewer.Window:
    return _load(viewer.new_window(OPEN_TIMEOUT), url, pdf, page)


def open_pdf(pdf: str) -> viewer.Window:
    """The window showing ``pdf``, as it is, else a new window that opens it."""
    _check(pdf)
    return viewer.showing(pdf) or _into_new_window(viewer.file_url(pdf), pdf)


def forward(tex: str, line: int, pdf: str) -> viewer.Window:
    """Show the place of ``tex``:``line`` in ``pdf``, in the window showing it or a new one."""
    _check(pdf)
    page = sourcelines.forward_page(tex, line, pdf, SYNCTEX_TIMEOUT)
    if page is None:
        raise Failure(f"no SyncTeX data for {tex}:{line} in {pdf}")
    url = viewer.file_url(pdf, f"src:{line} {tex}")
    w = viewer.showing(pdf)
    if w is None:
        return _into_new_window(url, pdf, page)
    return _load(w.service, url, pdf, page)


def run(argv: list[str]) -> int:
    """The exit code for ``argv``; messages on stdout/stderr."""
    args = parse_args(argv)
    if "help" in args:
        sys.stdout.write(HELP)
        return 0
    if "error" in args:
        sys.stderr.write(f"{PROG}: {args['error']}\n{USAGE}")
        return 2
    try:
        w = (forward(args["tex"], args["line"], args["pdf"]) if "tex" in args
             else open_pdf(args["pdf"]))
    except (Failure, viewer.ViewerError, OSError) as e:
        sys.stderr.write(f"{PROG}: {e}\n")
        return 1
    sys.stdout.write(f"{w.path}: page {w.page} of {w.pages}\n")
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
