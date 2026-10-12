import io
import json
import os
import signal
import time

import pytest

from okular_mcp import hook, selection, viewer


def run(monkeypatch, capsys, stdin):
    """Run hook.main() in-process; returns (exit code, stdout)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as e:
        hook.main()
    out = capsys.readouterr().out
    return e.value.code, out


def event(cwd):
    return json.dumps({"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": "hi",
                       "cwd": cwd})


@pytest.fixture(autouse=True)
def no_selection(monkeypatch):
    """C7: the Okular hook never reads the selection, even when one exists."""
    reads = []
    monkeypatch.setattr(selection, "read", lambda source="primary": reads.append(source) or "SELECTED TEXT")
    yield
    assert reads == []


# --- C6: output shape and exit code ---

def test_c6_prints_exactly_one_json_object(monkeypatch, capsys, two_windows, pdfs):
    code, out = run(monkeypatch, capsys, event(os.path.dirname(pdfs["a"])))
    assert code == 0
    assert out.endswith("\n") and out.count("\n") == 1
    obj = json.loads(out)
    assert set(obj) == {"hookSpecificOutput"}
    assert set(obj["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    assert obj["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null", "{}", '{"cwd": 5}', '{"cwd": ""}',
                                   '{"cwd": null}', '{"cwd": "/tmp"'])
def test_c6_garbage_stdin_exits_0_silently(monkeypatch, capsys, two_windows, stdin):
    assert run(monkeypatch, capsys, stdin) == (0, "")


def test_c6_viewer_error_exits_0_silently(monkeypatch, capsys, no_okular, tmp_path):
    assert run(monkeypatch, capsys, event(str(tmp_path))) == (0, "")


@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyError("path"), OSError("bus"), KeyboardInterrupt()])
def test_c6_internal_error_exits_0_silently(monkeypatch, capsys, tmp_path, exc):
    def active():
        raise exc
    monkeypatch.setattr(viewer, "active", active)
    assert run(monkeypatch, capsys, event(str(tmp_path))) == (0, "")


def test_c6_stdout_failure_still_exits_0(monkeypatch, two_windows, pdfs):
    class Broken(io.StringIO):
        def write(self, s):
            raise BrokenPipeError
    monkeypatch.setattr("sys.stdin", io.StringIO(event(os.path.dirname(pdfs["a"]))))
    monkeypatch.setattr("sys.stdout", Broken())
    with pytest.raises(SystemExit) as e:
        hook.main()
    assert e.value.code == 0


def test_c6_lowers_the_qdbus_timeout_and_restores_it(monkeypatch, capsys, pdfs):
    seen = []

    def active():
        seen.append(viewer.TIMEOUT)
        raise viewer.ViewerError("x")
    monkeypatch.setattr(viewer, "active", active)
    run(monkeypatch, capsys, event(os.path.dirname(pdfs["a"])))
    assert seen == [hook.QDBUS_TIMEOUT] and hook.QDBUS_TIMEOUT < 1
    assert viewer.TIMEOUT == 5.0


def test_c6_watchdog_ends_a_slow_hook_silently_with_0(monkeypatch, capsys, pdfs):
    monkeypatch.setattr(hook, "WATCHDOG", 0.05)

    def slow():  # a qdbus call that hangs
        time.sleep(5)
    monkeypatch.setattr(viewer, "active", slow)
    t = time.monotonic()
    assert run(monkeypatch, capsys, event(os.path.dirname(pdfs["a"]))) == (0, "")
    assert time.monotonic() - t < 1
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) is not hook._alarm


def test_c6_watchdog_defaults_inside_a_second():
    assert hook.WATCHDOG < 1


def test_c6_watchdog_cancelled_after_a_normal_run(monkeypatch, capsys, two_windows, pdfs):
    run(monkeypatch, capsys, event(os.path.dirname(pdfs["a"])))
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) is not hook._alarm


# --- C7: the text, never a selection ---

def test_c7_exact_text_without_selection(monkeypatch, capsys, two_windows, pdfs):
    _, out = run(monkeypatch, capsys, event(os.path.dirname(pdfs["a"])))
    text = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert text == f"The user is reading {pdfs['a']} in Okular, page 3 of 10."
    assert "SELECTED TEXT" not in out and "Selected text" not in out


# --- C8: only the active entry, only inside cwd ---

def test_c8_pdf_inside_cwd(monkeypatch, capsys, two_windows, pdfs, tmp_path):
    _, out = run(monkeypatch, capsys, event(str(tmp_path)))  # tmp_path/papers/a.pdf
    assert pdfs["a"] in out


def test_c8_cwd_equal_to_the_pdfs_directory(monkeypatch, capsys, two_windows, pdfs):
    _, out = run(monkeypatch, capsys, event(os.path.dirname(pdfs["a"])))
    assert pdfs["a"] in out


def test_c8_pdf_outside_cwd(monkeypatch, capsys, two_windows, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    assert run(monkeypatch, capsys, event(str(tmp_path / "elsewhere"))) == (0, "")


def test_c8_sibling_with_common_prefix_is_outside(monkeypatch, capsys, bus, tmp_path):
    """/x/pap is not a parent of /x/papers/a.pdf."""
    (tmp_path / "papers").mkdir()
    (tmp_path / "pap").mkdir()
    bus({1: {"path": str(tmp_path / "papers" / "a.pdf"), "page": 1, "pages": 2}}, {"org.kde.okular-1": 1})
    assert run(monkeypatch, capsys, event(str(tmp_path / "pap"))) == (0, "")


def test_c8_cwd_inside_the_pdfs_directory_is_outside(monkeypatch, capsys, two_windows, pdfs, tmp_path):
    sub = tmp_path / "papers" / "sub"
    sub.mkdir()
    assert run(monkeypatch, capsys, event(str(sub))) == (0, "")


def test_c8_only_the_active_entry_counts(monkeypatch, capsys, bus, tmp_path):
    (tmp_path / "proj").mkdir()
    bus({1: {"path": "/elsewhere/a.pdf", "page": 1, "pages": 2},
         2: {"path": str(tmp_path / "proj" / "b.pdf"), "page": 1, "pages": 2}},
        {"org.kde.okular-1": 1, "org.kde.okular-2": 2})
    assert run(monkeypatch, capsys, event(str(tmp_path / "proj"))) == (0, "")


def test_c8_symlinked_cwd_compares_real_paths(monkeypatch, capsys, two_windows, pdfs, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "papers")
    _, out = run(monkeypatch, capsys, event(str(link)))
    assert pdfs["a"] in out


@pytest.mark.parametrize("path, directory, expected", [
    ("/a/b/c.pdf", "/a/b", True),
    ("/a/b/c.pdf", "/a/b/", True),
    ("/a/b/c.pdf", "/", True),
    ("/a/bc/c.pdf", "/a/b", False),
    ("/a/c.pdf", "/a/b", False),
])
def test_c8_inside(path, directory, expected):
    assert hook.inside(path, directory) is expected
