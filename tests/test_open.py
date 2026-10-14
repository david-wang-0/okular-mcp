"""okular-mcp-open: the argument parsing and the open/forward flows, against the fake bus.

The new-window path is faked at ``viewer.new_window``; nothing starts Okular.
"""

import os
import time

import pytest

from okular_mcp import open as cli
from okular_mcp import sourcelines, viewer


def run(capsys, *argv):
    code = cli.run(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# --- arguments (the same command line as vscode-pdf-mcp-open) ---

def test_parse_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.parse_args(["a.pdf"]) == {"pdf": str(tmp_path / "a.pdf")}
    assert cli.parse_args(["--", "--odd.pdf"]) == {"pdf": str(tmp_path / "--odd.pdf")}


@pytest.mark.parametrize("argv", [
    ["--tex", "m.tex", "--line", "12", "a.pdf"],
    ["--tex=m.tex", "--line=12", "a.pdf"],
    ["a.pdf", "--line", "12", "--tex", "m.tex"],
])
def test_parse_forward(tmp_path, monkeypatch, argv):
    monkeypatch.chdir(tmp_path)
    assert cli.parse_args(argv) == {"pdf": str(tmp_path / "a.pdf"), "tex": str(tmp_path / "m.tex"), "line": 12}


@pytest.mark.parametrize("column, expected", [("3", {"column": 3}), ("0", {"column": 0}), ("-1", {})])
def test_parse_column_is_accepted_negative_is_none(column, expected):
    args = cli.parse_args(["--tex", "/m.tex", "--line", "2", "--column", column, "/a.pdf"])
    assert args == {"pdf": "/a.pdf", "tex": "/m.tex", "line": 2, **expected}


@pytest.mark.parametrize("argv, error", [
    ([], "no PDF given"),
    (["a.pdf", "b.pdf"], "give exactly one PDF"),
    (["--tex"], "--tex needs a value"),
    (["--page", "3", "a.pdf"], "unknown option --page"),
    (["--line", "3", "a.pdf"], "--line and --column need --tex"),
    (["--column", "3", "a.pdf"], "--line and --column need --tex"),
    (["--tex", "m.tex", "a.pdf"], "--tex needs --line <n>, a positive integer"),
    (["--tex", "m.tex", "--line", "0", "a.pdf"], "--tex needs --line <n>, a positive integer"),
    (["--tex", "m.tex", "--line", "x", "a.pdf"], "--tex needs --line <n>, a positive integer"),
    (["--tex", "m.tex", "--line", "2", "--column", "x", "a.pdf"], "--column must be an integer"),
])
def test_parse_errors(argv, error):
    assert cli.parse_args(argv) == {"error": error}


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_exits_0(capsys, flag):
    code, out, err = run(capsys, "x.pdf", flag)
    assert code == 0 and out.startswith(cli.USAGE) and "--column" in out and "ignored" in out and err == ""


def test_bad_arguments_exit_2_with_usage(capsys, two_windows):
    code, out, err = run(capsys, "--line", "3", "a.pdf")
    assert (code, out) == (2, "")
    assert err == f"okular-mcp-open: --line and --column need --tex\n{cli.USAGE}"
    assert two_windows.calls == []


# --- open ---

@pytest.fixture
def new_windows(monkeypatch, two_windows):
    """Fake viewer.new_window: each call adds an empty Okular (pid 300, 301, ...) to the bus."""
    started = []
    monkeypatch.setattr(viewer, "POLL", 0)

    def new_window(timeout=15.0):
        pid = 300 + len(started)
        two_windows.procs[pid] = {"path": "", "page": 0, "pages": 0}
        two_windows.names[f"org.kde.okular-{pid}"] = pid
        started.append(f"org.kde.okular-{pid}")
        return started[-1]
    monkeypatch.setattr(viewer, "new_window", new_window)
    return started


def test_open_shown_pdf_is_left_as_it_is(capsys, two_windows, pdfs, new_windows):
    assert run(capsys, pdfs["b"]) == (0, f"{pdfs['b']}: page 1 of 4\n", "")
    assert two_windows.methods() == [] and new_windows == []


def test_open_unshown_pdf_in_a_new_window(capsys, two_windows, pdfs, new_windows):
    assert run(capsys, pdfs["c"]) == (0, f"{pdfs['c']}: page 1 of 5\n", "")
    assert new_windows == ["org.kde.okular-300"]
    assert two_windows.methods() == [("org.kde.okular-300", "openDocument", viewer.file_url(pdfs["c"]))]


def test_open_when_okular_is_not_running(capsys, no_okular, pdfs, monkeypatch):
    monkeypatch.setattr(viewer, "POLL", 0)

    def new_window(timeout=15.0):
        no_okular.procs[7] = {"path": "", "page": 0, "pages": 0}
        no_okular.names["org.kde.okular-7"] = 7
        return "org.kde.okular-7"
    monkeypatch.setattr(viewer, "new_window", new_window)
    assert run(capsys, pdfs["a"]) == (0, f"{pdfs['a']}: page 1 of 9\n", "")


def test_open_load_failure_exits_1(capsys, two_windows, pdfs, new_windows, monkeypatch):
    monkeypatch.setattr(cli, "OPEN_TIMEOUT", 0.05)
    two_windows.docs[pdfs["c"]] = None
    assert run(capsys, pdfs["c"]) == (1, "", f"okular-mcp-open: cannot open {pdfs['c']}\n")


def test_open_broken_pdf_behind_an_error_dialog_exits_1_at_once(capsys, two_windows, pdfs, new_windows):
    """No wait for the document to show: Okular did not answer and shows it with no pages."""
    two_windows.docs[pdfs["c"]] = "dialog"
    t = time.monotonic()
    assert run(capsys, pdfs["c"]) == (1, "", f"okular-mcp-open: cannot open {pdfs['c']}\n")
    assert time.monotonic() - t < 1


def test_open_slow_load_that_finishes_exits_0(capsys, two_windows, pdfs, new_windows, monkeypatch):
    monkeypatch.setattr(viewer, "POLL", 0)
    two_windows.docs[pdfs["c"]] = "slow"
    assert run(capsys, pdfs["c"]) == (0, f"{pdfs['c']}: page 1 of 9\n", "")


def test_open_missing_file_exits_1(capsys, two_windows, tmp_path, new_windows):
    gone = str(tmp_path / "gone.pdf")
    assert run(capsys, gone) == (1, "", f"okular-mcp-open: no such file: {gone}\n")
    assert two_windows.calls == [] and new_windows == []


def test_okular_does_not_start_exits_1(capsys, two_windows, pdfs, monkeypatch):
    def new_window(timeout=15.0):
        raise viewer.ViewerError("Okular exited (status 1) before it was on the session bus")
    monkeypatch.setattr(viewer, "new_window", new_window)
    code, out, err = run(capsys, pdfs["c"])
    assert (code, out) == (1, "") and err.startswith("okular-mcp-open: Okular exited")


# --- forward search ---

@pytest.fixture
def synctex_page(monkeypatch):
    """Fake sourcelines.forward_page: ``pages`` maps (tex, line) to the page."""
    pages, calls = {}, []

    def forward_page(tex, line, pdf, timeout=5.0):
        calls.append((tex, line, pdf))
        return pages.get((tex, line))
    monkeypatch.setattr(sourcelines, "forward_page", forward_page)
    return pages, calls


def test_forward_in_the_window_showing_the_pdf(capsys, two_windows, pdfs, new_windows, synctex_page, tmp_path):
    pages, calls = synctex_page
    tex = str(tmp_path / "sec one" / "intro.tex")
    pages[(tex, 40)] = 7
    two_windows.forward[f"src:40 {tex}"] = 7
    two_windows.docs[pdfs["a"]] = 10
    assert run(capsys, "--tex", tex, "--line", "40", "--column", "-1", pdfs["a"]) == (
        0, f"{pdfs['a']}: page 7 of 10\n", "")
    assert calls == [(tex, 40, pdfs["a"])]
    assert two_windows.methods() == [("org.kde.okular-100", "openDocument", viewer.file_url(pdfs["a"], f"src:40 {tex}"))]
    assert two_windows.fragments == [f"src:40 {tex}"] and new_windows == []


def test_forward_broken_pdf_in_the_window_showing_it_exits_1(capsys, two_windows, pdfs, new_windows, synctex_page, tmp_path):
    pages, _ = synctex_page
    tex = str(tmp_path / "a.tex")
    pages[(tex, 3)] = 2
    two_windows.docs[pdfs["a"]] = "dialog"
    assert run(capsys, "--tex", tex, "--line", "3", pdfs["a"]) == (
        1, "", f"okular-mcp-open: cannot open {pdfs['a']}\n")


def test_forward_into_a_new_window(capsys, two_windows, pdfs, new_windows, synctex_page):
    pages, _ = synctex_page
    pages[("/t/m.tex", 3)] = 2
    two_windows.forward["src:3 /t/m.tex"] = 2
    assert run(capsys, "--tex", "/t/m.tex", "--line", "3", pdfs["c"]) == (0, f"{pdfs['c']}: page 2 of 5\n", "")
    assert two_windows.methods() == [("org.kde.okular-300", "openDocument", viewer.file_url(pdfs["c"], "src:3 /t/m.tex"))]


def test_forward_reports_what_okular_shows(capsys, two_windows, pdfs, new_windows, synctex_page, monkeypatch):
    monkeypatch.setattr(cli, "OPEN_TIMEOUT", 0.2)
    pages, _ = synctex_page
    pages[("/t/m.tex", 3)] = 2
    two_windows.forward["src:3 /t/m.tex"] = 5
    assert run(capsys, "--tex", "/t/m.tex", "--line", "3", pdfs["b"]) == (0, f"{pdfs['b']}: page 5 of 9\n", "")


def test_forward_without_synctex_result_exits_1(capsys, two_windows, pdfs, new_windows, synctex_page):
    code, out, err = run(capsys, "--tex", "/t/m.tex", "--line", "3", pdfs["a"])
    assert (code, out) == (1, "")
    assert err == f"okular-mcp-open: no SyncTeX data for /t/m.tex:3 in {pdfs['a']}\n"
    assert two_windows.calls == [] and new_windows == []


def test_forward_missing_pdf_exits_1(capsys, two_windows, tmp_path, synctex_page):
    gone = str(tmp_path / "gone.pdf")
    assert run(capsys, "--tex", "/t/m.tex", "--line", "3", gone) == (1, "", f"okular-mcp-open: no such file: {gone}\n")
    assert synctex_page[1] == []


def test_main_exits_with_the_code(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["okular-mcp-open"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2


def test_console_script_is_registered():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert 'okular-mcp-open = "okular_mcp.open:main"' in open(os.path.join(root, "pyproject.toml")).read()
