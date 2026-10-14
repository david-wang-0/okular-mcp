"""A fake session bus behind ``viewer._call``, shared by the viewer, server and hook tests.

Nothing here talks to a real D-Bus or starts Okular.
"""

from urllib.parse import unquote, urlsplit

import pytest

from okular_mcp import viewer

DBUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.GetConnectionUnixProcessID")
READS = ("currentDocument", "currentPage", "pages")


class Bus:
    """``procs`` maps pid -> {"path", "page", "pages"}; ``names`` maps D-Bus name -> pid
    (None makes the pid lookup fail). ``broken`` pids fail their property calls.
    ``docs`` maps a path to its page count, for openDocument (default 9 pages; None
    makes the load fail: Okular is then left with no document; "dialog" makes it fail
    the way a broken PDF does: Okular shows the path with no pages and does not answer,
    as its modal error dialog is open; "slow" loads 9 pages but does not answer in
    time either). openDocument takes a
    file:// URL; ``forward`` maps its fragment (``src:<line> <tex>``) to the page Okular
    then shows (default 1), and ``fragments`` records the fragments it got.
    """

    def __init__(self, procs, names, broken=(), docs=None):
        self.procs, self.names, self.broken = procs, names, set(broken)
        self.docs, self.calls = docs or {}, []
        self.forward, self.fragments = {}, []

    def __call__(self, *args):
        self.calls.append(args)
        if not args:  # ListNames, as qdbus prints it once stripped
            return "\n ".join(["org.freedesktop.DBus", ":1.42", *self.names, "org.kde.kwin"])
        if args[:3] == DBUS:
            pid = self.names.get(args[3])
            if pid is None:
                raise viewer.ViewerError("no pid")
            return str(pid)
        svc, obj, method, *rest = args
        assert obj == "/okular" and method.startswith("org.kde.okular.")
        pid = self.names[svc]
        if pid in self.broken:
            raise viewer.ViewerError("call failed")
        p = self.procs[pid]
        name = method.rsplit(".", 1)[1]
        if name == "currentDocument":
            return p["path"]
        if name in ("currentPage", "pages"):
            return str(p["page" if name == "currentPage" else "pages"])
        if name == "openDocument":
            url = urlsplit(rest[0])
            assert url.scheme == "file", "openDocument takes a file:// URL"
            path, fragment = unquote(url.path), unquote(url.fragment)
            self.fragments.append(fragment)
            n = self.docs.get(path, 9)
            page = self.forward.get(fragment, 1)
            if n in ("dialog", "slow"):
                p.update(path=path, page=0 if n == "dialog" else page, pages=0 if n == "dialog" else 9)
                raise viewer.Timeout(f"qdbus timed out: {' '.join(args)}")
            p.update(path=path, page=page, pages=n) if n else p.update(path="", page=0, pages=0)
        elif name == "goToPage":
            p["page"] = int(rest[0])
        else:
            raise AssertionError(f"unexpected Okular method {name}")
        return ""

    def methods(self, reads=False):
        """(service, method, *args) of the Okular calls, without the property reads unless ``reads``."""
        return [(a[0], a[2].rsplit(".", 1)[1], *a[3:]) for a in self.calls
                if a and a[:3] != DBUS and (reads or a[2].rsplit(".", 1)[1] not in READS)]


@pytest.fixture
def bus(monkeypatch):
    def install(procs, names, broken=(), docs=None):
        b = Bus(procs, names, broken, docs)
        monkeypatch.setattr(viewer, "_call", b)
        return b
    return install


@pytest.fixture
def pdfs(tmp_path):
    """Three existing files a.pdf, b.pdf, c.pdf under tmp_path/papers, as strings."""
    d = tmp_path / "papers"
    d.mkdir()
    out = {}
    for n in "abc":
        (d / f"{n}.pdf").write_bytes(b"%PDF-1.4\n")
        out[n] = str(d / f"{n}.pdf")
    return out


@pytest.fixture
def two_windows(bus, pdfs):
    """Okular 100 shows a.pdf p3/10, Okular 200 shows b.pdf p1/4; each has two bus names."""
    return bus({100: {"path": pdfs["a"], "page": 3, "pages": 10},
                200: {"path": pdfs["b"], "page": 1, "pages": 4}},
               {"org.kde.okular-100": 100, "org.kde.okular.instance-100": 100,
                "org.kde.okular-200": 200, "org.kde.okular.instance-200": 200},
               docs={pdfs["c"]: 5})


@pytest.fixture
def no_okular(bus):
    return bus({}, {})


# --- fake pdftotext and synctex behind sourcelines._run_all ---

def box(i, j):
    """The grid the fake text layer puts word ``j`` of printed line ``i`` on: (x0, y0, x1, y1)."""
    return 50.0 + 60 * j, 100.0 + 20 * i, 90.0 + 60 * j, 110.0 + 20 * i


class Tools:
    """Fake ``pdftotext -bbox-layout`` and ``synctex edit``/``view`` (CONTRACT.md C2 ``tex``).

    ``pages`` maps a page number to its printed lines, each a string of words (boxes on
    the ``box`` grid). ``edit`` maps (page, line i, word j) to the ``Input`` and
    ``Line`` that ``synctex edit`` reports at that word's centre; ``views`` maps
    (line, file) to the (page, x, y) results of ``synctex view``. ``fail`` maps a
    tool name to an exception to raise. ``calls`` records each batch of commands.
    """

    def __init__(self, pages=None, edit=None, views=None):
        self.pages, self.edit, self.views = pages or {}, edit or {}, views or {}
        self.fail, self.calls = {}, []

    def __call__(self, cmds, deadline):
        self.calls.append(cmds)
        for c in cmds:
            if c[0] in self.fail:
                raise self.fail[c[0]]
        return [self.run(c) for c in cmds]

    def run(self, cmd):
        if cmd[0] == "pdftotext":
            first, last = int(cmd[2]), min(int(cmd[4]), max(self.pages, default=0))
            return self.xhtml(range(first, last + 1))  # pages not in ``pages`` are empty
        if cmd[:2] == ["synctex", "edit"]:
            page, x, y, _ = cmd[3].split(":", 3)
            for (p, i, j), (inp, line) in self.edit.items():
                x0, y0, x1, y1 = box(i, j)
                if p == int(page) and x0 <= float(x) <= x1 and y0 <= float(y) <= y1:
                    return ("This is SyncTeX command line utility, version 1.5\nSyncTeX result begin\n"
                            f"Output:/x.pdf\nInput:{inp}\nLine:{line}\nColumn:-1\nOffset:0\nContext:\n"
                            "SyncTeX result end\n")
            return "This is SyncTeX command line utility, version 1.5\n"
        if cmd[:2] == ["synctex", "view"]:
            line, _, file = cmd[3].split(":", 2)
            res = self.views.get((int(line), file), [])
            if not res:
                return f"SyncTeX Warning: No tag for {file}\n"
            return ("SyncTeX result begin\n" + "".join(
                f"Output:/x.pdf\nPage:{p}\nx:{x}\ny:{y}\nh:0\nv:0\nW:10\nH:10\nbefore:\noffset:-1\nmiddle:\nafter:\n"
                for p, x, y in res) + "SyncTeX result end\n")
        raise AssertionError(f"unexpected command {cmd}")

    def xhtml(self, numbers):
        out = ['<?xml version="1.0"?>\n<html><body>\n<doc>']
        for n in numbers:
            out.append('  <page width="612.000000" height="792.000000">\n    <flow>\n      <block>')
            for i, line in enumerate(self.pages.get(n, [])):
                out.append('        <line xMin="0" yMin="0" xMax="0" yMax="0">')
                for j, word in enumerate(line.split()):
                    x0, y0, x1, y1 = box(i, j)
                    word = word.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    out.append(f'          <word xMin="{x0:f}" yMin="{y0:f}" xMax="{x1:f}" yMax="{y1:f}">{word}</word>')
                out.append("        </line>")
            out.append("      </block>\n    </flow>\n  </page>")
        out.append("</doc>\n</body>\n</html>")
        return "\n".join(out)


@pytest.fixture
def tools(monkeypatch):
    from okular_mcp import sourcelines

    def install(pages=None, edit=None, views=None):
        t = Tools(pages, edit, views)
        monkeypatch.setattr(sourcelines, "_run_all", t)
        return t
    return install
