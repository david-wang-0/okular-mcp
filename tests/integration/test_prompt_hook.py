"""okular-mcp-hook against real headless Okulars, with a fake primary selection."""

import json
import os

import pytest

pytestmark = pytest.mark.integration


def hook(session, cwd):
    code, out, err, secs = session.run("okular-mcp-hook", stdin=json.dumps({"cwd": str(cwd), "prompt": "hi"}))
    assert (code, err) == (0, "")
    assert secs < 1, f"took {secs:.2f} s"
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None


def test_c7_selection_on_the_page_with_source_lines_relative_to_cwd(session):
    d = session.docs
    s = session.start(d["main"])
    session.go(s, 3)
    session.fake.set(primary="The quick brown fox")
    assert hook(session, d["proj"]) == (
        f"The user is reading {d['main']} in Okular, page 3 of 5.\nSelected text (page 3):\n"
        "The quick brown fox\nSource: sec/part.tex:3")


def test_c7_selection_on_the_next_page(session):
    d = session.docs
    s = session.start(d["main"])
    session.go(s, 3)
    session.fake.set(primary="delta words")
    assert hook(session, d["proj"]) == (
        f"The user is reading {d['main']} in Okular, page 3 of 5.\nSelected text (page 4):\n"
        "delta words\nSource: main.tex:9")


def test_c7_selection_not_on_the_page_is_left_out(session):
    d = session.docs
    s = session.start(d["main"])
    session.go(s, 3)
    session.fake.set(primary="$ git status")
    assert hook(session, d["proj"]) == f"The user is reading {d['main']} in Okular, page 3 of 5."


def test_c8_relevance_by_cwd_and_symlinks(session, tmp_path):
    d = session.docs
    session.start(d["main"])
    os.symlink(d["proj"], tmp_path / "link")
    assert hook(session, tmp_path / "link") == f"The user is reading {d['main']} in Okular, page 1 of 5."
    assert hook(session, os.path.join(d["proj"], "sec")) is None
    assert hook(session, d["docs"]) is None


def test_c6_c8_nothing_open_prints_nothing(session):
    assert hook(session, session.docs["proj"]) is None
    session.start()
    assert hook(session, session.docs["proj"]) is None
