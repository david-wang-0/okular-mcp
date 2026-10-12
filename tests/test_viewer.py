import os
import subprocess

import pytest

from conftest import DBUS
from okular_mcp import viewer


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
    assert two_windows.methods() == [("org.kde.okular-100", "openDocument", pdfs["c"]),
                                     ("org.kde.okular-100", "goToPage", "5")]
    seq = two_windows.methods(reads=True)
    i_open = seq.index(("org.kde.okular-100", "openDocument", pdfs["c"]))
    i_go = seq.index(("org.kde.okular-100", "goToPage", "5"))
    assert ("org.kde.okular-100", "pages") in seq[i_open + 1:i_go]
    assert e == entry("org.kde.okular-100", pdfs["c"], 5, 5, True)


def test_c3_opens_into_first_window_even_without_a_document(bus, pdfs):
    b = bus({1: {"path": "", "page": 0, "pages": 0}}, {"org.kde.okular-1": 1}, docs={pdfs["a"]: 3})
    assert viewer.goto(2, pdfs["a"]) == entry("org.kde.okular-1", pdfs["a"], 2, 3, True)
    assert b.methods() == [("org.kde.okular-1", "openDocument", pdfs["a"]), ("org.kde.okular-1", "goToPage", "2")]


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
    assert ("org.kde.okular-100", "openDocument", pdfs["c"]) in two_windows.methods()
    assert not [m for m in two_windows.methods() if m[1] == "goToPage"]


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
