import io
import json
import os
import signal
import time

import pytest

from okular_mcp import hook, selection, sourcelines, viewer


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
def sel(monkeypatch):
    """A fake primary selection (nothing selected unless a test sets ``sel.text``)."""
    class Sel:
        text, reads, error = "", [], None

        def __call__(self, source="primary", timeout=5.0):
            self.reads.append((source, timeout))
            if self.error:
                raise self.error
            return self.text
    s = Sel()
    s.reads = []
    monkeypatch.setattr(selection, "read", s)
    return s


def additional(monkeypatch, capsys, cwd):
    code, out = run(monkeypatch, capsys, event(cwd))
    assert code == 0
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


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


# --- C7: the text; the selection only when its words are on the page ---

PAGE3 = {2: ["the previous page"], 3: ["The quick brown fox jumps", "over the lazy dog"],
         4: ["the next page", "has other words"]}


@pytest.fixture
def synctex(pdfs):
    """SyncTeX data next to a.pdf (the fake tools never read it)."""
    open(pdfs["a"][:-len(".pdf")] + ".synctex.gz", "w").close()


def test_c7_exact_text_without_selection(monkeypatch, capsys, two_windows, pdfs, sel):
    text = additional(monkeypatch, capsys, os.path.dirname(pdfs["a"]))
    assert text == f"The user is reading {pdfs['a']} in Okular, page 3 of 10."
    assert sel.reads and sel.reads[0][0] == "primary"


def test_c7_selection_found_on_the_current_page(monkeypatch, capsys, two_windows, pdfs, sel, tools):
    tools(PAGE3)
    sel.text = "lazy dog"
    assert additional(monkeypatch, capsys, os.path.dirname(pdfs["a"])) == (
        f"The user is reading {pdfs['a']} in Okular, page 3 of 10.\nSelected text (page 3):\nlazy dog")


def test_c7_selection_on_the_next_page_keeps_the_page_sentence(monkeypatch, capsys, two_windows, pdfs, sel, tools):
    tools(PAGE3)
    sel.text = "has other\nwords"
    assert additional(monkeypatch, capsys, os.path.dirname(pdfs["a"])) == (
        f"The user is reading {pdfs['a']} in Okular, page 3 of 10.\nSelected text (page 4):\nhas other\nwords")


def test_c7_selection_not_on_these_pages_is_left_out(monkeypatch, capsys, two_windows, pdfs, sel, tools):
    """The primary selection may come from the terminal the prompt is typed in."""
    tools(PAGE3)
    sel.text = "$ git status"
    assert additional(monkeypatch, capsys, os.path.dirname(pdfs["a"])) == (
        f"The user is reading {pdfs['a']} in Okular, page 3 of 10.")


def test_c7_source_lines(monkeypatch, capsys, two_windows, pdfs, sel, tools, synctex, tmp_path):
    inside_tex = str(tmp_path / "papers" / "sec" / "intro.tex")
    outside_tex = "/elsewhere/macros.tex"
    tools({3: ["The quick brown fox jumps", "over the lazy dog", "and runs far away"]},
          edit={(3, 0, 3): (inside_tex, 12), (3, 1, 2): (inside_tex, 14), (3, 2, 1): (outside_tex, 7)})
    sel.text = "fox jumps over the lazy dog and runs"
    assert additional(monkeypatch, capsys, str(tmp_path / "papers")) == (
        f"The user is reading {pdfs['a']} in Okular, page 3 of 10.\nSelected text (page 3):\n"
        "fox jumps over the lazy dog and runs\nSource: sec/intro.tex:12-14\nSource: /elsewhere/macros.tex:7")


def test_c7_no_source_lines_without_synctex_data(monkeypatch, capsys, two_windows, pdfs, sel, tools):
    tools(PAGE3, edit={(3, 1, 2): ("/x/a.tex", 4)})
    sel.text = "lazy"
    assert additional(monkeypatch, capsys, os.path.dirname(pdfs["a"])).endswith("Selected text (page 3):\nlazy")


def test_c7_no_source_lines_for_an_ambiguous_selection(monkeypatch, capsys, two_windows, pdfs, sel, tools, synctex):
    tools(PAGE3, edit={(3, 0, 0): ("/x/a.tex", 4), (3, 1, 1): ("/x/a.tex", 5)})
    sel.text = "the"
    assert "Source:" not in additional(monkeypatch, capsys, os.path.dirname(pdfs["a"]))


def test_c7_selection_cut_at_2000_characters(monkeypatch, capsys, two_windows, pdfs, sel):
    monkeypatch.setattr(sourcelines, "locate", lambda *a, **k: sourcelines.Match(3, 1, []))
    sel.text = "x" * 2001
    text = additional(monkeypatch, capsys, os.path.dirname(pdfs["a"]))
    assert text.endswith("\nSelected text (page 3):\n" + "x" * 2000 + " […]")
    sel.text = "y" * 2000
    assert additional(monkeypatch, capsys, os.path.dirname(pdfs["a"])).endswith("\n" + "y" * 2000)


@pytest.mark.parametrize("error", [selection.SelectionError("no clipboard tool found"),
                                   selection.SelectionError("wl-paste timed out"), OSError("x")])
def test_c7_selection_failure_keeps_the_page_sentence(monkeypatch, capsys, two_windows, pdfs, sel, error):
    sel.error = error
    assert additional(monkeypatch, capsys, os.path.dirname(pdfs["a"])) == (
        f"The user is reading {pdfs['a']} in Okular, page 3 of 10.")


def test_c7_slow_tools_time_out_and_keep_the_page_sentence(monkeypatch, capsys, two_windows, pdfs, sel):
    """pdftotext hanging: the selection part gives up at its deadline, well inside the watchdog."""
    real = sourcelines.subprocess.Popen
    monkeypatch.setattr(sourcelines.subprocess, "Popen", lambda cmd, **kw: real(["sleep", "5"], **kw))
    sel.text = "lazy dog"
    t = time.monotonic()
    assert additional(monkeypatch, capsys, os.path.dirname(pdfs["a"])) == (
        f"The user is reading {pdfs['a']} in Okular, page 3 of 10.")
    assert time.monotonic() - t < hook.WATCHDOG


def test_c7_every_step_gets_the_time_left(monkeypatch, capsys, two_windows, pdfs, sel, tools, synctex):
    tools(PAGE3, edit={(3, 1, 2): ("/x/a.tex", 4)})
    seen = []
    for name in ("locate", "tex_lines"):
        f = getattr(sourcelines, name)
        monkeypatch.setattr(sourcelines, name, lambda *a, f=f, name=name, **k: seen.append((name, k["timeout"])) or f(*a, **k))
    sel.text = "lazy"
    assert "Source: /x/a.tex:4" in additional(monkeypatch, capsys, os.path.dirname(pdfs["a"]))
    timeouts = [sel.reads[0][1]] + [t for _, t in seen]
    assert [n for n, _ in seen] == ["locate", "tex_lines"]
    assert all(0 < t <= hook.SELECTION_DEADLINE for t in timeouts)
    assert timeouts == sorted(timeouts, reverse=True)
    assert hook.SELECTION_DEADLINE < hook.WATCHDOG


def test_c7_no_time_left_skips_the_selection(monkeypatch, capsys, two_windows, pdfs, sel):
    monkeypatch.setattr(hook, "SELECTION_DEADLINE", 0.0)
    sel.text = "lazy dog"
    assert additional(monkeypatch, capsys, os.path.dirname(pdfs["a"])) == (
        f"The user is reading {pdfs['a']} in Okular, page 3 of 10.")
    assert sel.reads == []


def test_c7_context_without_a_deadline_has_no_selection(two_windows, pdfs, sel):
    sel.text = "lazy dog"
    assert hook.context({"cwd": os.path.dirname(pdfs["a"])}) == (
        f"The user is reading {pdfs['a']} in Okular, page 3 of 10.")
    assert sel.reads == []


@pytest.mark.parametrize("file, cwd, expected", [
    ("/p/sec/a.tex", "/p", "sec/a.tex"),
    ("/p/a.tex", "/p/", "a.tex"),
    ("/p/./sec/../a.tex", "/p", "a.tex"),
    ("/q/a.tex", "/p", "/q/a.tex"),
    ("/pq/a.tex", "/p", "/pq/a.tex"),
    ("/p", "/p", "p"),
])
def test_c7_relative_to_cwd(file, cwd, expected):
    assert hook.relative_to_cwd(file, cwd) == expected


def test_c7_relative_to_cwd_after_resolving_symlinks(tmp_path):
    (tmp_path / "real" / "sec").mkdir(parents=True)
    tex = tmp_path / "real" / "sec" / "a.tex"
    tex.write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert hook.relative_to_cwd(str(tex), str(tmp_path / "link")) == "sec/a.tex"
    assert hook.relative_to_cwd(str(tmp_path / "link" / "sec" / "a.tex"), str(tmp_path / "real")) == "sec/a.tex"


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
