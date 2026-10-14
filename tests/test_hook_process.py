"""The ``okular-mcp-hook`` executable as Claude Code runs it: a process reading the event
on stdin, with the fake ``qdbus``, ``wl-paste``, ``pdftotext`` and ``synctex`` of
tests/fakes.py on ``PATH`` (CONTRACT.md C6-C8).
"""

import json
import os
import subprocess
import time

import pytest

from fakes import FakeBin, entry_point
from mcpclient import coverage_env

HOOK = entry_point("okular-mcp-hook")
GARBAGE = [b"", b"garbage{", b"null", b"[1,2]", b'"str"', b"{}", b'{"cwd": 42}', b'{"cwd": ""}',
           b'{"cwd": "/tmp"', b"\xff\xfe\x00", b'{"cwd": "a\\u0000b"}']


@pytest.fixture
def world(tmp_path):
    class World:
        pass
    w = World()
    w.fake = FakeBin(tmp_path / "fake")
    w.rt = tmp_path / "rt"
    w.rt.mkdir(mode=0o700)
    (w.rt / "bus").touch()
    (w.rt / "wayland-0").touch()
    w.proj = tmp_path / "proj"
    (w.proj / "sec").mkdir(parents=True)
    w.pdf = str(w.proj / "main.pdf")
    open(w.pdf, "wb").write(b"%PDF-1.4\n")
    w.env = {"PATH": w.fake.path, "XDG_RUNTIME_DIR": str(w.rt), "HOME": str(tmp_path), **coverage_env()}
    return w


def run(w, stdin, env=None):
    """(exit code, stdout, stderr, seconds)."""
    if isinstance(stdin, dict):
        stdin = json.dumps({"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": "hi", **stdin})
    if isinstance(stdin, str):
        stdin = stdin.encode()
    t = time.monotonic()
    r = subprocess.run(HOOK, input=stdin, env=env or w.env, capture_output=True, timeout=10)
    return r.returncode, r.stdout.decode(), r.stderr.decode(), time.monotonic() - t


def context(w, cwd, env=None):
    code, out, err, secs = run(w, {"cwd": str(cwd)}, env)
    assert (code, err) == (0, "")
    assert secs < 1, f"took {secs:.2f} s"
    if not out:
        return None
    assert out.endswith("\n") and out.count("\n") == 1, out
    obj = json.loads(out)
    assert obj == {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": obj["hookSpecificOutput"]["additionalContext"]}}
    return obj["hookSpecificOutput"]["additionalContext"]


# --- C6 ---

@pytest.mark.parametrize("stdin", GARBAGE)
def test_c6_garbage_empty_or_non_object_stdin_exits_0_silently(world, stdin):
    world.fake.windows((100, world.pdf, 3, 10))
    assert run(world, stdin)[:3] == (0, "", "")


def test_c6_prints_exactly_one_json_object_well_inside_a_second(world):
    world.fake.windows((100, world.pdf, 3, 10))
    assert context(world, world.proj) == f"The user is reading {world.pdf} in Okular, page 3 of 10."


@pytest.mark.parametrize("state", [
    {"fail": {"qdbus": "Could not connect to D-Bus server"}},
    {"garbage": {"qdbus": "org.kde.okular-1\n"}},  # a name whose pid lookup prints nonsense
    {"names": {"org.kde.okular-1": 1}, "procs": {"1": {"path": "/p/a.pdf", "page": "x", "pages": "y"}}},
])
def test_c6_internal_errors_exit_0_silently(world, state):
    world.fake.set(**state)
    assert run(world, {"cwd": str(world.proj)})[:3] == (0, "", "")


def test_c6_no_qdbus_on_path_exits_0_silently(world, tmp_path):
    env = {**world.env, "PATH": str(tmp_path / "empty")}
    assert run(world, {"cwd": str(world.proj)}, env)[:3] == (0, "", "")


def test_c6_slow_qdbus_is_cut_off_by_the_watchdog_inside_a_second(world):
    world.fake.windows((100, world.pdf, 3, 10))
    world.fake.set(slow={"qdbus": 5})
    code, out, err, secs = run(world, {"cwd": str(world.proj)})
    assert (code, out, err) == (0, "", "")
    assert secs < 1, f"took {secs:.2f} s"


def test_c6_slow_selection_tools_keep_the_page_sentence(world):
    world.fake.windows((100, world.pdf, 3, 10))
    world.fake.set(primary="lazy dog", slow={"pdftotext": 5})
    assert context(world, world.proj) == f"The user is reading {world.pdf} in Okular, page 3 of 10."


# --- C7 ---

def test_c7_selected_text_and_source_lines_when_found(world):
    world.fake.windows((100, world.pdf, 3, 10))
    open(world.pdf[:-4] + ".synctex.gz", "w").close()
    outside = "/elsewhere/macros.tex"
    world.fake.set(primary="fox jumps\nover the lazy dog",
                   layer={"3": ["The quick brown fox jumps", "over the lazy dog", "and runs far away"]},
                   edit=[[3, 0, 3, "sec/intro.tex", 12], [3, 1, 3, "sec/intro.tex", 13], [3, 1, 2, outside, 7]])
    assert context(world, world.proj) == (
        f"The user is reading {world.pdf} in Okular, page 3 of 10.\nSelected text (page 3):\n"
        "fox jumps\nover the lazy dog\nSource: sec/intro.tex:12-13")
    world.fake.set(primary="lazy")
    assert context(world, world.proj).endswith("\nSelected text (page 3):\nlazy\nSource: /elsewhere/macros.tex:7")
    assert world.fake.calls("pdftotext")


def test_c7_selection_on_the_page_before_names_that_page(world):
    world.fake.windows((100, world.pdf, 3, 10))
    world.fake.set(primary="previous words", layer={"2": ["the previous words"], "3": ["other"]})
    assert context(world, world.proj) == (
        f"The user is reading {world.pdf} in Okular, page 3 of 10.\nSelected text (page 2):\nprevious words")


def test_c7_page_only_when_the_selection_is_not_on_the_page(world):
    world.fake.windows((100, world.pdf, 3, 10))
    world.fake.set(primary="$ git status", layer={"3": ["the lazy dog"]})
    assert context(world, world.proj) == f"The user is reading {world.pdf} in Okular, page 3 of 10."


def test_c7_page_only_when_nothing_is_selected_or_no_selection_tool(world):
    world.fake.windows((100, world.pdf, 3, 10))
    assert context(world, world.proj) == f"The user is reading {world.pdf} in Okular, page 3 of 10."
    world.fake.set(primary="lazy dog", layer={"3": ["the lazy dog"]}, fail={"wl-paste": "Can't open display"})
    assert context(world, world.proj) == f"The user is reading {world.pdf} in Okular, page 3 of 10."


def test_c7_the_selection_is_cut_at_2000_characters(world):
    world.fake.windows((100, world.pdf, 1, 1))
    world.fake.set(primary="x" * 2001, layer={"1": ["x" * 2001]})
    assert context(world, world.proj).endswith("\nSelected text (page 1):\n" + "x" * 2000 + " […]")


# --- C8 ---

def test_c8_relevance_by_cwd(world, tmp_path):
    world.fake.windows((100, world.pdf, 3, 10))
    (tmp_path / "pro").mkdir()
    assert context(world, world.proj) is not None
    assert context(world, str(world.proj) + "/") is not None
    assert context(world, tmp_path) is not None
    assert context(world, world.proj / "sec") is None
    assert context(world, tmp_path / "pro") is None  # a prefix of proj, not a parent


def test_c8_only_the_active_entry_counts(world, tmp_path):
    world.fake.windows((1, "", 0, 0), (100, "/elsewhere/a.pdf", 1, 2), (200, world.pdf, 1, 2))
    assert context(world, world.proj) is None


def test_c8_nothing_open_prints_nothing(world):
    assert context(world, world.proj) is None
    world.fake.windows((1, "", 0, 0))
    assert context(world, world.proj) is None


def test_c8_relevance_holds_through_symlinks(world, tmp_path):
    (tmp_path / "link").symlink_to(world.proj)
    world.fake.windows((100, world.pdf, 3, 10))
    assert context(world, tmp_path / "link") == f"The user is reading {world.pdf} in Okular, page 3 of 10."
    world.fake.windows((100, str(tmp_path / "link" / "main.pdf"), 3, 10))
    assert context(world, world.proj) is not None


def test_c5_the_hook_fills_the_session_variables_first(world):
    world.fake.windows((100, world.pdf, 3, 10))
    world.fake.set(primary="")
    context(world, world.proj)
    assert {(c["tool"], c["env"]["DBUS_SESSION_BUS_ADDRESS"], c["env"]["WAYLAND_DISPLAY"])
            for c in world.fake.calls()} == {("qdbus", f"unix:path={world.rt}/bus", "wayland-0"),
                                             ("wl-paste", f"unix:path={world.rt}/bus", "wayland-0")}
