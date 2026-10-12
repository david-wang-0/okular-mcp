"""A fake session bus behind ``viewer._call``, shared by the viewer, server and hook tests.

Nothing here talks to a real D-Bus or starts Okular.
"""

import pytest

from okular_mcp import viewer

DBUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.GetConnectionUnixProcessID")
READS = ("currentDocument", "currentPage", "pages")


class Bus:
    """``procs`` maps pid -> {"path", "page", "pages"}; ``names`` maps D-Bus name -> pid
    (None makes the pid lookup fail). ``broken`` pids fail their property calls.
    ``docs`` maps a path to its page count, for openDocument (default 9 pages; None
    makes the load fail: Okular is then left with no document).
    """

    def __init__(self, procs, names, broken=(), docs=None):
        self.procs, self.names, self.broken = procs, names, set(broken)
        self.docs, self.calls = docs or {}, []

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
            n = self.docs.get(rest[0], 9)
            p.update(path=rest[0], page=1, pages=n) if n else p.update(path="", page=0, pages=0)
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
