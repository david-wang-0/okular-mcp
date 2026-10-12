import subprocess

import pytest

from okular_mcp import viewer

DBUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.GetConnectionUnixProcessID")


class Bus:
    """Fake session bus behind ``viewer._call``.

    ``procs`` maps pid -> {"path", "page", "pages"}; ``names`` maps D-Bus name -> pid
    (None makes the pid lookup fail). ``broken`` pids fail their property calls.
    """

    def __init__(self, procs, names, broken=()):
        self.procs, self.names, self.broken, self.calls = procs, names, set(broken), []

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
        assert obj == "/okular"
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
            p.update(path=rest[0], page=1)
        elif name == "goToPage":
            p["page"] = int(rest[0])
        return ""

    def methods(self):
        return [(a[0], a[2].rsplit(".", 1)[1], *a[3:]) for a in self.calls if a and a[:3] != DBUS
                and a[2].rsplit(".", 1)[1] not in ("currentDocument", "currentPage", "pages")]


@pytest.fixture
def bus(monkeypatch):
    def install(procs, names, broken=()):
        b = Bus(procs, names, broken)
        monkeypatch.setattr(viewer, "_call", b)
        return b
    return install


def two_windows(bus):
    return bus({100: {"path": "/a.pdf", "page": 3, "pages": 10}, 200: {"path": "/b.pdf", "page": 1, "pages": 4}},
               {"org.kde.okular-100": 100, "org.kde.okular.instance-100": 100,
                "org.kde.okular-200": 200, "org.kde.okular.instance-200": 200})


def test_windows_one_per_process_first_name_wins(bus):
    two_windows(bus)
    assert viewer.windows() == [viewer.ViewerWindow("org.kde.okular-100", 100, "/a.pdf", 3, 10),
                                viewer.ViewerWindow("org.kde.okular-200", 200, "/b.pdf", 1, 4)]
    assert viewer.windows()[0].viewer == "okular"


def test_windows_ignores_non_okular_services(bus):
    b = two_windows(bus)
    viewer.windows()
    asked = {a[3] for a in b.calls if a[:3] == DBUS}
    assert asked == set(b.names)


def test_windows_skips_service_whose_pid_lookup_fails(bus):
    bus({200: {"path": "/b.pdf", "page": 1, "pages": 4}}, {"org.kde.okular-1": None, "org.kde.okular-200": 200})
    assert [w.pid for w in viewer.windows()] == [200]


def test_windows_skips_service_whose_property_call_fails(bus):
    bus({100: {"path": "/a.pdf", "page": 1, "pages": 1}, 200: {"path": "/b.pdf", "page": 1, "pages": 4}},
        {"org.kde.okular-100": 100, "org.kde.okular-200": 200}, broken={100})
    assert [w.pid for w in viewer.windows()] == [200]


def test_windows_empty_page_counts_as_zero(bus):
    """A window with no document reports path '' and page/pages 0."""
    bus({1: {"path": "", "page": "", "pages": ""}}, {"org.kde.okular-1": 1})
    (w,) = viewer.windows()
    assert (w.path, w.page, w.pages) == ("", 0, 0)


def test_windows_none_raises(bus):
    bus({}, {})
    with pytest.raises(viewer.ViewerError, match="is Okular running"):
        viewer.windows()


def test_current_is_first_window_with_a_document(bus):
    bus({1: {"path": "", "page": 0, "pages": 0}, 2: {"path": "/b.pdf", "page": 2, "pages": 4}},
        {"org.kde.okular-1": 1, "org.kde.okular-2": 2})
    assert viewer.current().path == "/b.pdf"


def test_current_without_document_raises(bus):
    bus({1: {"path": "", "page": 0, "pages": 0}}, {"org.kde.okular-1": 1})
    with pytest.raises(viewer.ViewerError, match="no document open"):
        viewer.current()


def test_find(bus):
    two_windows(bus)
    assert viewer.find("/b.pdf").pid == 200
    assert viewer.find("/c.pdf") is None


def test_goto_shown_document_does_not_reopen(bus):
    b = two_windows(bus)
    w = viewer.goto(7, "/b.pdf")
    assert b.methods() == [("org.kde.okular-200", "goToPage", "7")]
    assert (w.path, w.page) == ("/b.pdf", 7)


def test_goto_unshown_document_opens_it_in_first_window(bus):
    b = two_windows(bus)
    w = viewer.goto(5, "/c.pdf")
    assert b.methods() == [("org.kde.okular-100", "openDocument", "/c.pdf"),
                           ("org.kde.okular-100", "goToPage", "5")]
    assert (w.pid, w.path, w.page) == (100, "/c.pdf", 5)


def test_goto_without_path_uses_current(bus):
    b = two_windows(bus)
    w = viewer.goto(2)
    assert b.methods() == [("org.kde.okular-100", "goToPage", "2")]
    assert (w.path, w.page) == ("/a.pdf", 2)


def test_reload_window_showing_path(bus):
    b = two_windows(bus)
    viewer.reload("/b.pdf")
    assert b.methods() == [("org.kde.okular-200", "reload")]


def test_reload_noop_when_not_shown(bus):
    b = two_windows(bus)
    viewer.reload("/c.pdf")
    assert b.methods() == []


# --- _call and _qdbus, against a fake subprocess.run / PATH ---

@pytest.fixture
def which(monkeypatch):
    monkeypatch.setattr(viewer, "ensure_session_env", lambda: None)

    def install(*names):
        monkeypatch.setattr(viewer.shutil, "which", lambda n: f"/usr/bin/{n}" if n in names else None)
    return install


@pytest.mark.parametrize("present, chosen", [
    (("qdbus", "qdbus6", "qdbus-qt6", "qdbus-qt5"), "qdbus"),
    (("qdbus6", "qdbus-qt6", "qdbus-qt5"), "qdbus6"),
    (("qdbus-qt6", "qdbus-qt5"), "qdbus-qt6"),
    (("qdbus-qt5",), "qdbus-qt5"),
])
def test_qdbus_lookup_order(which, present, chosen):
    which(*present)
    assert viewer._qdbus() == f"/usr/bin/{chosen}"


def test_qdbus_missing_raises(which):
    which()
    with pytest.raises(viewer.ViewerError, match="qdbus not found"):
        viewer._qdbus()


def test_call_strips_output_and_passes_argv(which, monkeypatch):
    which("qdbus")
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, " org.kde.okular-1\n org.kde.okular-2\n", "")
    monkeypatch.setattr(viewer.subprocess, "run", run)
    assert viewer._call("svc", "/okular", "m", "3") == "org.kde.okular-1\n org.kde.okular-2"
    assert seen == [["/usr/bin/qdbus", "svc", "/okular", "m", "3"]]


def test_call_nonzero_exit_raises(which, monkeypatch):
    which("qdbus")
    monkeypatch.setattr(viewer.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Service not found\n"))
    with pytest.raises(viewer.ViewerError, match="Service not found"):
        viewer._call("svc")


def test_call_timeout_raises(which, monkeypatch):
    which("qdbus")

    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    monkeypatch.setattr(viewer.subprocess, "run", run)
    with pytest.raises(viewer.ViewerError, match="timed out"):
        viewer._call("svc")
