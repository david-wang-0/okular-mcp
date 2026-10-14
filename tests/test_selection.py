import subprocess

import pytest

from okular_mcp import selection


class Run:
    """Fake subprocess.run: records argv, returns a canned result or raises."""

    def __init__(self, stdout="", rc=0, stderr="", exc=None):
        self.result = subprocess.CompletedProcess([], rc, stdout, stderr)
        self.exc, self.calls = exc, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def fake(monkeypatch):
    """Returns setup(tools, wayland, **run) -> Run, with only ``tools`` on PATH."""
    monkeypatch.setattr(selection, "ensure_session_env", lambda: None)

    def setup(tools=(), wayland=False, **run):
        monkeypatch.setattr(selection.shutil, "which", lambda n: f"/usr/bin/{n}" if n in tools else None)
        if wayland:
            monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        else:
            monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        r = Run(**run)
        monkeypatch.setattr(selection.subprocess, "run", r)
        return r

    return setup


@pytest.mark.parametrize("source, argv", [
    ("primary", ["wl-paste", "--primary", "--no-newline"]),
    ("clipboard", ["wl-paste", "--no-newline"]),
])
def test_c2_wayland_uses_wl_paste(fake, source, argv):
    r = fake(("wl-paste", "xclip", "xsel"), wayland=True, stdout="hi")
    assert selection.read(source) == "hi" and r.calls == [argv]


@pytest.mark.parametrize("source", ["primary", "clipboard"])
def test_c2_x11_uses_xclip(fake, source):
    r = fake(("wl-paste", "xclip", "xsel"), wayland=False)
    selection.read(source)
    assert r.calls == [["xclip", "-o", "-selection", source]]


def test_wayland_without_wl_paste_falls_back_to_xclip(fake):
    r = fake(("xclip",), wayland=True)
    selection.read()
    assert r.calls == [["xclip", "-o", "-selection", "primary"]]


@pytest.mark.parametrize("source, flag", [("primary", "--primary"), ("clipboard", "--clipboard")])
def test_c2_xsel_when_no_xclip(fake, source, flag):
    r = fake(("xsel",))
    selection.read(source)
    assert r.calls == [["xsel", "-o", flag]]


def test_no_tool_raises(fake):
    r = fake(())
    with pytest.raises(selection.SelectionError) as e:
        selection.read()
    assert str(e.value) == "no clipboard tool found: install wl-clipboard (Wayland) or xclip/xsel (X11)"
    assert r.calls == []


def test_c2_clipboard_tool_failure_names_the_command(fake):
    fake(("xclip",), rc=1, stderr="Can't open display\n")
    with pytest.raises(selection.SelectionError) as e:
        selection.read("clipboard")
    assert str(e.value) == "xclip -o -selection clipboard failed: Can't open display"


@pytest.mark.parametrize("source", ["secondary", "", "PRIMARY", "Clipboard"])
def test_c2_any_other_source_raises_before_running_anything(fake, source):
    r = fake(("wl-paste", "xclip", "xsel"), wayland=True)
    with pytest.raises(selection.SelectionError) as e:
        selection.read(source)
    assert str(e.value) == "source must be 'primary' or 'clipboard'"
    assert r.calls == []


def test_c2_bad_source_raises_before_running_anything(fake):
    r = fake(("xclip",))
    with pytest.raises(selection.SelectionError):
        selection.read("secondary")
    assert r.calls == []


def test_default_source_is_primary(fake):
    r = fake(("xclip",))
    selection.read()
    assert r.calls[0][-1] == "primary"


def test_stdout_returned_verbatim(fake):
    fake(("xclip",), stdout="  two\nlines \n")
    assert selection.read() == "  two\nlines \n"


@pytest.mark.parametrize("stderr", ["Nothing is copied", "Error: No selection", "xclip: no selection\n"])
def test_c2_empty_selection_is_empty_string(fake, stderr):
    fake(("xclip",), rc=1, stderr=stderr, stdout="junk")
    assert selection.read() == ""


def test_other_failure_raises(fake):
    fake(("xclip",), rc=1, stderr="Can't open display")
    with pytest.raises(selection.SelectionError, match="Can't open display"):
        selection.read()


def test_timeout_raises(fake):
    fake(("xclip",), exc=subprocess.TimeoutExpired("xclip", 5))
    with pytest.raises(selection.SelectionError, match="timed out"):
        selection.read()


def test_timeout_is_passed_on(fake, monkeypatch):
    fake(("xclip",))
    seen = []
    monkeypatch.setattr(selection.subprocess, "run",
                        lambda cmd, **kw: seen.append(kw["timeout"]) or subprocess.CompletedProcess(cmd, 0, "", ""))
    selection.read(timeout=0.25)
    selection.read()
    assert seen == [0.25, 5.0]
