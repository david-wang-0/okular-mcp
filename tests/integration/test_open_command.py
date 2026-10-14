"""okular-mcp-open against real headless Okulars: open, reuse, forward search, exit codes."""

import os

import pytest

pytestmark = pytest.mark.integration


def shown(session):
    """{service: (path, page, pages)} of the Okular windows on the bus."""
    return {s: session.state(s) for s in session.services()}


def test_open_in_a_new_window(session):
    paper = session.docs["paper"]
    code, out, err, _ = session.run("okular-mcp-open", paper)
    assert (code, out, err) == (0, f"{paper}: page 1 of 5\n", "")
    assert list(shown(session).values()) == [(paper, 1, 5)]


def test_open_reuses_the_window_that_shows_the_pdf_as_it_is(session):
    paper = session.docs["paper"]
    assert session.run("okular-mcp-open", paper)[0] == 0
    (service,) = session.services()
    session.go(service, 3)
    code, out, err, _ = session.run("okular-mcp-open", paper)
    assert (code, out, err) == (0, f"{paper}: page 3 of 5\n", "")
    assert shown(session) == {service: (paper, 3, 5)}
    assert session.run("okular-mcp-open", session.docs["link"])[1] == f"{paper}: page 3 of 5\n"
    assert session.services() == [service]


def test_forward_search_into_an_input_file_new_window_then_reused(session):
    d = session.docs
    code, out, err, _ = session.run("okular-mcp-open", "--tex", d["part"], "--line", "3", d["main"])
    assert (code, out, err) == (0, f"{d['main']}: page 3 of 5\n", "")
    (service,) = session.services()
    session.go(service, 1)
    code, out, err, _ = session.run("okular-mcp-open", "--tex", "sec/part.tex", "--line", "1", "--column", "-1",
                                    "main.pdf", cwd=d["proj"])
    assert (code, out, err) == (0, f"{d['main']}: page 3 of 5\n", "")
    assert shown(session) == {service: (d["main"], 3, 5)}
    code, out, _, _ = session.run("okular-mcp-open", "--tex", os.path.join(d["proj"], "main.tex"), "--line", "9",
                                  d["main"])
    assert (code, out) == (0, f"{d['main']}: page 4 of 5\n")


def test_forward_search_without_synctex_data_exits_1(session):
    d = session.docs
    code, out, err, _ = session.run("okular-mcp-open", "--tex", d["part"], "--line", "3", d["nosync"])
    assert (code, out, err) == (1, "", f"okular-mcp-open: no SyncTeX data for {d['part']}:3 in {d['nosync']}\n")
    assert session.services() == []


def test_exit_codes(session):
    code, out, err, _ = session.run("okular-mcp-open")
    assert (code, out) == (2, "") and err.startswith("okular-mcp-open: no PDF given\nusage: ")
    assert session.run("okular-mcp-open", "--line", "3", session.docs["paper"])[0] == 2
    gone = os.path.join(session.docs["docs"], "gone.pdf")
    assert session.run("okular-mcp-open", gone)[:3] == (1, "", f"okular-mcp-open: no such file: {gone}\n")
    assert session.services() == []


def test_a_broken_pdf_exits_1_with_cannot_open(session):
    broken = session.docs["broken"]
    code, out, err, secs = session.run("okular-mcp-open", broken)
    assert (code, out, err) == (1, "", f"okular-mcp-open: cannot open {broken}\n")
    assert secs < 15
