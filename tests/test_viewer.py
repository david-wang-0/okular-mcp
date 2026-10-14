import os
import re
import subprocess
import time

import pytest

from conftest import DBUS
from okular_mcp import viewer
from okular_mcp.viewer import ViewerError


def entry(window, path, page, pages, active):
    return {"viewer": "okular", "window": window, "path": path, "page": page, "pages": pages,
            "active": active}


# --- C1: entries ---

def test_c1_one_entry_per_process_first_name_wins_active_first(two_windows, pdfs):
    assert viewer.entries() == [entry("org.kde.okular-100", pdfs["a"], 3, 10, True),
                                entry("org.kde.okular-200", pdfs["b"], 1, 4, False)]


def test_c1_exactly_one_active(two_windows):
    assert [e["active"] for e in viewer.entries()] == [True, False]


def test_c1_windows_without_a_document_are_excluded(bus):
    bus({1: {"path": "", "page": "", "pages": ""}, 2: {"path": "/b.pdf", "page": 2, "pages": 4},
         3: {"path": "", "page": 0, "pages": 0}, 4: {"path": "/d.pdf", "page": 1, "pages": 1}},
        {"org.kde.okular-1": 1, "org.kde.okular-2": 2, "org.kde.okular-3": 3, "org.kde.okular-4": 4})
    assert viewer.entries() == [entry("org.kde.okular-2", "/b.pdf", 2, 4, True),
                                entry("org.kde.okular-4", "/d.pdf", 1, 1, False)]
    assert viewer.active() == entry("org.kde.okular-2", "/b.pdf", 2, 4, True)


def test_c1_okular_not_running_error_prefix(no_okular):
    with pytest.raises(viewer.ViewerError, match=r"^no Okular window with a PDF open"):
        viewer.entries()
    with pytest.raises(viewer.ViewerError, match=r"^no Okular window with a PDF open"):
        viewer.active()


def test_c1_no_document_open_error_is_the_prefix(bus):
    bus({1: {"path": "", "page": 0, "pages": 0}}, {"org.kde.okular-1": 1})
    with pytest.raises(viewer.ViewerError) as e:
        viewer.entries()
    assert str(e.value) == "no Okular window with a PDF open"


def test_c1_bus_failure_keeps_the_prefix(monkeypatch):
    def fail(*args):
        raise viewer.ViewerError("qdbus not found on PATH (install qt6-tools / qt5-tools)")
    monkeypatch.setattr(viewer, "_call", fail)
    with pytest.raises(viewer.ViewerError, match=r"^no Okular window with a PDF open \(qdbus not found"):
        viewer.entries()


def test_c1_ignores_non_okular_services(two_windows):
    viewer.entries()
    asked = {a[3] for a in two_windows.calls if a[:3] == DBUS}
    assert asked == set(two_windows.names)


def test_c1_skips_service_whose_pid_lookup_fails(bus):
    bus({200: {"path": "/b.pdf", "page": 1, "pages": 4}}, {"org.kde.okular-1": None, "org.kde.okular-200": 200})
    assert [e["window"] for e in viewer.entries()] == ["org.kde.okular-200"]


def test_c1_skips_service_whose_property_call_fails(bus):
    bus({100: {"path": "/a.pdf", "page": 1, "pages": 1}, 200: {"path": "/b.pdf", "page": 1, "pages": 4}},
        {"org.kde.okular-100": 100, "org.kde.okular-200": 200}, broken={100})
    assert viewer.entries() == [entry("org.kde.okular-200", "/b.pdf", 1, 4, True)]


def test_c1_windows_lists_empty_windows_and_nothing_when_not_running(bus):
    """windows() is the raw list (goto opens into an empty window); entries() filters it."""
    bus({1: {"path": "", "page": "", "pages": ""}}, {"org.kde.okular-1": 1})
    assert viewer.windows() == [viewer.Window("org.kde.okular-1", 1, "", 0, 0)]
    bus({}, {})
    assert viewer.windows() == []


# --- C3: goto ---

def test_c3_shown_document_is_not_reopened(two_windows, pdfs):
    e = viewer.goto(4, pdfs["b"])
    assert two_windows.methods() == [("org.kde.okular-200", "goToPage", "4")]
    assert e == entry("org.kde.okular-200", pdfs["b"], 4, 4, False)


def test_c3_unshown_document_opens_in_first_window_then_checks_then_moves(two_windows, pdfs):
    e = viewer.goto(5, pdfs["c"])
    assert two_windows.methods() == [("org.kde.okular-100", "openDocument", viewer.file_url(pdfs["c"])),
                                     ("org.kde.okular-100", "goToPage", "5")]
    seq = two_windows.methods(reads=True)
    i_open = seq.index(("org.kde.okular-100", "openDocument", viewer.file_url(pdfs["c"])))
    i_go = seq.index(("org.kde.okular-100", "goToPage", "5"))
    assert ("org.kde.okular-100", "pages") in seq[i_open + 1:i_go]
    assert e == entry("org.kde.okular-100", pdfs["c"], 5, 5, True)


def test_c3_opens_into_first_window_even_without_a_document(bus, pdfs):
    b = bus({1: {"path": "", "page": 0, "pages": 0}}, {"org.kde.okular-1": 1}, docs={pdfs["a"]: 3})
    assert viewer.goto(2, pdfs["a"]) == entry("org.kde.okular-1", pdfs["a"], 2, 3, True)
    assert b.methods() == [("org.kde.okular-1", "openDocument", viewer.file_url(pdfs["a"])), ("org.kde.okular-1", "goToPage", "2")]


def test_c3_without_path_uses_the_active_entry(two_windows, pdfs):
    e = viewer.goto(2)
    assert two_windows.methods() == [("org.kde.okular-100", "goToPage", "2")]
    assert e == entry("org.kde.okular-100", pdfs["a"], 2, 10, True)


def test_c3_returned_page_is_the_requested_page(two_windows, pdfs, monkeypatch):
    """Even if Okular still reports the old page right after goToPage."""
    original = two_windows.__class__.__call__

    def lagging(self, *args):
        if args and args[2:3] == ("org.kde.okular.goToPage",):
            self.calls.append(args)
            return ""
        return original(self, *args)
    monkeypatch.setattr(two_windows.__class__, "__call__", lagging)
    assert viewer.goto(7, pdfs["a"])["page"] == 7


def test_c3_out_of_range_raised_before_goto(two_windows, pdfs):
    for page in (0, 11):
        with pytest.raises(ValueError, match=rf"^page {page} outside 1-10$"):
            viewer.goto(page, pdfs["a"])
    with pytest.raises(ValueError, match=r"^page 6 outside 1-5$"):
        viewer.goto(6, pdfs["c"])
    assert ("org.kde.okular-100", "openDocument", viewer.file_url(pdfs["c"])) in two_windows.methods()
    assert not [m for m in two_windows.methods() if m[1] == "goToPage"]


def test_c3_load_failure_is_cannot_open_before_the_page_check(two_windows, pdfs):
    two_windows.docs[pdfs["c"]] = None
    with pytest.raises(ViewerError, match=rf"^cannot open {re.escape(pdfs['c'])}$"):
        viewer.goto(99, pdfs["c"])
    assert not [m for m in two_windows.methods() if m[1] == "goToPage"]


def test_c3_load_failure_leaving_the_old_document_is_cannot_open(two_windows, pdfs, monkeypatch):
    original = two_windows.__class__.__call__

    def refusing(self, *args):
        if args and args[2:3] == ("org.kde.okular.openDocument",):
            self.calls.append(args)
            return ""
        return original(self, *args)
    monkeypatch.setattr(two_windows.__class__, "__call__", refusing)
    with pytest.raises(ViewerError, match=r"^cannot open "):
        viewer.goto(1, pdfs["c"])


def test_c3_broken_pdf_behind_an_error_dialog_is_cannot_open(two_windows, pdfs):
    """Okular answers openDocument for a file it cannot load only once its modal error
    dialog is closed: the qdbus timeout is reported as C3's message, before the page check."""
    two_windows.docs[pdfs["c"]] = "dialog"
    with pytest.raises(ViewerError) as e:
        viewer.goto(99, pdfs["c"])
    assert str(e.value) == f"cannot open {pdfs['c']}"
    assert not [m for m in two_windows.methods() if m[1] == "goToPage"]


def test_c3_slow_load_that_finishes_is_not_an_error(two_windows, pdfs):
    two_windows.docs[pdfs["c"]] = "slow"
    assert viewer.goto(4, pdfs["c"]) == entry("org.kde.okular-100", pdfs["c"], 4, 9, True)


def test_c3_no_such_file_before_any_okular_call(two_windows, tmp_path):
    gone = str(tmp_path / "gone.pdf")
    with pytest.raises(FileNotFoundError) as e:
        viewer.goto(1, gone)
    assert str(e.value) == f"no such file: {gone}"
    assert two_windows.calls == []


def test_c3_directory_is_no_such_file(two_windows, tmp_path):
    with pytest.raises(FileNotFoundError, match="^no such file: "):
        viewer.goto(1, str(tmp_path))


def test_c3_path_omitted_and_nothing_open_raises_c1(bus):
    b = bus({1: {"path": "", "page": 0, "pages": 0}}, {"org.kde.okular-1": 1})
    with pytest.raises(viewer.ViewerError, match=r"^no Okular window with a PDF open"):
        viewer.goto(1)
    assert b.methods() == []


def test_c3_okular_not_running_raises_c1(no_okular, pdfs):
    with pytest.raises(viewer.ViewerError, match=r"^no Okular window with a PDF open"):
        viewer.goto(1, pdfs["a"])
    with pytest.raises(viewer.ViewerError, match=r"^no Okular window with a PDF open"):
        viewer.goto(1)


def test_c3_relative_path_and_symlink_match_the_shown_document(two_windows, pdfs, tmp_path, monkeypatch):
    link = tmp_path / "link.pdf"
    link.symlink_to(pdfs["b"])
    viewer.goto(2, str(link))
    monkeypatch.chdir(os.path.dirname(pdfs["b"]))
    viewer.goto(3, "b.pdf")
    assert two_windows.methods() == [("org.kde.okular-200", "goToPage", "2"),
                                     ("org.kde.okular-200", "goToPage", "3")]


# --- a bus or window that does not answer: an error, not a hang (real qdbus processes) ---

@pytest.fixture
def slow_bus(tmp_path, monkeypatch):
    """The fake qdbus executable of tests/fakes.py first on PATH, a short TIMEOUT."""
    from fakes import FakeBin
    fake = FakeBin(tmp_path / "fake", tools=("qdbus",))
    monkeypatch.setenv("PATH", fake.path)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent")
    monkeypatch.setattr(viewer, "TIMEOUT", 0.5)
    return fake


def test_c3_a_window_that_does_not_answer_is_an_error_not_a_hang(slow_bus, pdfs):
    slow_bus.windows((100, pdfs["a"], 3, 10))
    slow_bus.set(slow={"goToPage": 30})
    t = time.monotonic()
    with pytest.raises(viewer.Timeout) as e:
        viewer.goto(2)
    assert str(e.value) == "qdbus timed out: org.kde.okular-100 /okular org.kde.okular.goToPage 2"
    assert time.monotonic() - t < 5


def test_c1_a_bus_that_does_not_answer_is_the_c1_error_with_the_cause(slow_bus):
    slow_bus.set(slow={"ListNames": 30})
    with pytest.raises(ViewerError) as e:
        viewer.entries()
    assert str(e.value) == "no Okular window with a PDF open (qdbus timed out: )"


def test_c1_a_window_that_does_not_answer_is_left_out(slow_bus, pdfs, monkeypatch):
    """One hung Okular does not hide the others."""
    slow_bus.windows((100, pdfs["a"], 3, 10), (200, pdfs["b"], 1, 4))
    real = viewer._okular

    def hung_100(service, method, *args):
        if slow_bus.state["names"][service] == 100:  # either of its two bus names
            raise viewer.Timeout("qdbus timed out")
        return real(service, method, *args)
    monkeypatch.setattr(viewer, "_okular", hung_100)
    assert [e["window"] for e in viewer.entries()] == ["org.kde.okular-200"]


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


def test_call_strips_output_passes_argv_and_timeout(which, monkeypatch):
    which("qdbus")
    monkeypatch.setattr(viewer, "TIMEOUT", 0.25)
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw["timeout"]))
        return subprocess.CompletedProcess(cmd, 0, " org.kde.okular-1\n org.kde.okular-2\n", "")
    monkeypatch.setattr(viewer.subprocess, "run", run)
    assert viewer._call("svc", "/okular", "m", "3") == "org.kde.okular-1\n org.kde.okular-2"
    assert seen == [(["/usr/bin/qdbus", "svc", "/okular", "m", "3"], 0.25)]


def test_call_default_timeout_is_five_seconds():
    assert viewer.TIMEOUT == 5.0


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


# --- opening documents: file_url, open_url, showing, new_window, wait_loaded ---

@pytest.mark.parametrize("path, fragment, url", [
    ("/a/b.pdf", None, "file:///a/b.pdf"),
    ("/a b/c#1%.pdf", None, "file:///a%20b/c%231%25.pdf"),
    ("/a/b.pdf", "src:12 /t x/m.tex", "file:///a/b.pdf#src:12%20/t%20x/m.tex"),
])
def test_file_url(path, fragment, url):
    assert viewer.file_url(path, fragment) == url
    assert url.count("#") == (fragment is not None)


def test_file_url_is_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert viewer.file_url("x.pdf") == "file://" + str(tmp_path / "x.pdf")


def test_c3_goto_opens_with_a_file_url(two_windows, tmp_path):
    """A bare path with a "#" in it hangs Okular."""
    odd = tmp_path / "a b#c.pdf"
    odd.write_bytes(b"%PDF-1.4\n")
    assert viewer.goto(2, str(odd))["path"] == str(odd)
    assert ("org.kde.okular-100", "openDocument", "file://" + str(tmp_path) + "/a%20b%23c.pdf") \
        in two_windows.methods()


def test_showing_compares_real_paths(two_windows, pdfs, tmp_path):
    link = tmp_path / "l.pdf"
    link.symlink_to(pdfs["b"])
    assert viewer.showing(str(link)).service == "org.kde.okular-200"
    assert viewer.showing(pdfs["c"]) is None


class FakeOkular:
    """Fake subprocess.Popen for ``okular``: the new process joins the fake bus after
    ``after`` polls (None: never), or has exited with ``status``."""

    pid = 4242

    def __init__(self, bus, after=1, status=None):
        self.bus, self.after, self.status, self.argv, self.kw, self.polls = bus, after, status, None, None, 0

    def __call__(self, argv, **kw):
        self.argv, self.kw = argv, kw
        return self

    def poll(self):
        self.polls += 1
        if self.status is None and self.after is not None and self.polls > self.after:
            self.bus.procs[self.pid] = {"path": "", "page": 0, "pages": 0}
            self.bus.names[f"org.kde.okular-{self.pid}"] = self.pid
        return self.status

    @property
    def returncode(self):
        return self.status


@pytest.fixture
def launch(monkeypatch, which):
    which("qdbus", "okular")
    monkeypatch.setattr(viewer, "POLL", 0)

    def install(bus, **kw):
        fake = FakeOkular(bus, **kw)
        monkeypatch.setattr(viewer.subprocess, "Popen", fake)
        return fake
    return install


def test_new_window_starts_okular_without_arguments_detached(two_windows, launch):
    fake = launch(two_windows, after=3)
    assert viewer.new_window(5) == "org.kde.okular-4242"
    assert fake.argv == ["/usr/bin/okular"]
    assert fake.kw["start_new_session"] is True
    assert all(fake.kw[k] == subprocess.DEVNULL for k in ("stdin", "stdout", "stderr"))


def test_new_window_okular_exits(two_windows, launch):
    launch(two_windows, status=1)
    with pytest.raises(ViewerError, match=r"^Okular exited \(status 1\)"):
        viewer.new_window(5)


def test_new_window_never_appears(two_windows, launch):
    launch(two_windows, after=None)
    with pytest.raises(ViewerError, match="did not appear on the session bus within 0.05 s"):
        viewer.new_window(0.05)


def test_new_window_without_okular(two_windows, which):
    which("qdbus")
    with pytest.raises(ViewerError, match="okular not found"):
        viewer.new_window(1)


def test_wait_loaded_returns_once_on_the_page(two_windows, pdfs, monkeypatch):
    monkeypatch.setattr(viewer, "POLL", 0)
    w = viewer.wait_loaded("org.kde.okular-100", pdfs["a"], page=3, timeout=5)
    assert (w.path, w.page, w.pages) == (pdfs["a"], 3, 10)
    assert len([m for m in two_windows.methods(reads=True) if m[1] == "currentDocument"]) == 1


def test_wait_loaded_without_page_waits_for_it_to_settle(two_windows, pdfs, monkeypatch):
    monkeypatch.setattr(viewer, "POLL", 0)
    assert viewer.wait_loaded("org.kde.okular-100", pdfs["a"], timeout=5, settle=3).page == 3
    assert len([m for m in two_windows.methods(reads=True) if m[1] == "currentDocument"]) == 3


def test_wait_loaded_takes_another_page_once_it_stays(two_windows, pdfs, monkeypatch):
    monkeypatch.setattr(viewer, "POLL", 0)
    assert viewer.wait_loaded("org.kde.okular-100", pdfs["a"], page=7, timeout=5, settle=2).page == 3


def test_wait_loaded_cannot_open(two_windows, pdfs, monkeypatch):
    monkeypatch.setattr(viewer, "POLL", 0.01)
    with pytest.raises(ViewerError, match=rf"^cannot open {re.escape(pdfs['c'])}$"):
        viewer.wait_loaded("org.kde.okular-100", pdfs["c"], timeout=0.05)


def test_open_url_reports_whether_okular_answered(two_windows, pdfs):
    assert viewer.open_url("org.kde.okular-100", viewer.file_url(pdfs["c"])) is True
    two_windows.docs[pdfs["a"]] = "dialog"
    assert viewer.open_url("org.kde.okular-100", viewer.file_url(pdfs["a"])) is False


def test_call_timeout_is_a_viewer_error():
    assert issubclass(viewer.Timeout, ViewerError)


def test_open_url_is_open_document(two_windows):
    viewer.open_url("org.kde.okular-200", "file:///x.pdf#src:3%20/t.tex")
    assert two_windows.methods() == [("org.kde.okular-200", "openDocument", "file:///x.pdf#src:3%20/t.tex")]
    assert two_windows.fragments == ["src:3 /t.tex"]


# --- the rarer turns: windows closing, Okular slow to answer or to settle ---

def test_c3_window_closed_while_moving_is_an_error(two_windows, pdfs, monkeypatch):
    original = two_windows.__class__.__call__

    def closing(self, *args):
        out = original(self, *args)
        if args and args[2:3] == ("org.kde.okular.goToPage",):
            del self.names["org.kde.okular-100"], self.names["org.kde.okular.instance-100"]
        return out
    monkeypatch.setattr(two_windows.__class__, "__call__", closing)
    with pytest.raises(ViewerError) as e:
        viewer.goto(2)
    assert str(e.value) == "Okular window org.kde.okular-100 closed while moving to page 2"


def test_new_window_keeps_polling_while_the_bus_fails(two_windows, launch, monkeypatch):
    """A bus call failing while Okular starts is not fatal: the next look may find it."""
    fake = launch(two_windows, after=2)
    real, failures = viewer._services, []

    def flaky():
        if len(failures) < 2:
            failures.append(1)
            raise ViewerError("qdbus timed out: ")
        return real()
    monkeypatch.setattr(viewer, "_services", flaky)
    assert viewer.new_window(5) == "org.kde.okular-4242"
    assert len(failures) == 2 and fake.polls >= 3


def test_wait_loaded_keeps_polling_while_the_window_does_not_answer(two_windows, pdfs, monkeypatch):
    monkeypatch.setattr(viewer, "POLL", 0)
    real, failures = viewer.window, []

    def flaky(service):
        if len(failures) < 2:
            failures.append(1)
            raise ViewerError("qdbus timed out: ")
        return real(service)
    monkeypatch.setattr(viewer, "window", flaky)
    assert viewer.wait_loaded("org.kde.okular-100", pdfs["a"], page=3, timeout=5).page == 3
    assert len(failures) == 2


def test_wait_loaded_takes_the_window_as_it_is_at_the_timeout(two_windows, pdfs, monkeypatch):
    """The page never settles (a forward search still scrolling): the last look counts."""
    monkeypatch.setattr(viewer, "POLL", 0.01)
    real, pages = viewer.window, iter(range(1, 10_000))

    def scrolling(service):
        w = real(service)
        w.page = next(pages)
        return w
    monkeypatch.setattr(viewer, "window", scrolling)
    w = viewer.wait_loaded("org.kde.okular-100", pdfs["a"], page=99, timeout=0.1)
    assert w.path == pdfs["a"] and w.page > 1
