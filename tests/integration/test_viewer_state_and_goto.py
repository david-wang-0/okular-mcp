"""C1-C3 against real headless Okulars, through the real okular-mcp server over stdio."""

import os
import re
import time

import pytest

from mcpclient import data, text

pytestmark = pytest.mark.integration

NO_DOCUMENT = "no Okular window with a PDF open"
NOT_RUNNING = f"{NO_DOCUMENT} (Okular is not running on the session bus)"


def message(result):
    assert result.is_error, text(result)
    return re.sub(r"^Error executing tool \w+: ", "", text(result))


def entry(service, path, page, pages, active):
    return {"viewer": "okular", "window": service, "path": path, "page": page, "pages": pages, "active": active}


def listed(mcp):
    """viewer_state's entries, as a list even for one."""
    d = data(mcp.call("viewer_state"))
    return d if isinstance(d, list) else [d]


# --- C1 ---

def test_c1_okular_not_running(mcp):
    assert message(mcp.call("viewer_state")) == NOT_RUNNING


def test_c1_okular_running_without_a_document(session, mcp):
    session.start()
    assert message(mcp.call("viewer_state")) == NO_DOCUMENT


def test_c1_windows_in_bus_order_the_first_with_a_document_active(session, mcp):
    d = session.docs
    a = session.start(d["paper"])
    session.start()
    b = session.start(d["other"])
    session.go(b, 4)
    order = [s for s in session.services() if s in (a, b)]
    expected = {a: entry(a, d["paper"], 1, 5, False), b: entry(b, d["other"], 4, 5, False)}
    got = listed(mcp)
    assert [e["window"] for e in got] == order
    assert [e["active"] for e in got] == [True, False]
    assert got == [{**expected[s], "active": i == 0} for i, s in enumerate(order)]


# --- C2 with a selection on the real page text ---

def test_c2_selection_page_and_tex_from_the_real_document(session, mcp):
    d = session.docs
    s = session.start(d["main"])
    session.go(s, 3)
    session.fake.set(primary="charlie words")
    assert data(mcp.call("get_selection")) == {
        "text": "charlie words", "source": "primary", "viewer": "okular", "path": d["main"], "page": 3,
        "tex": [{"file": d["part"], "lines": [1, 1]}]}
    session.fake.set(primary="delta words")  # on the next page
    got = data(mcp.call("get_selection"))
    assert (got["page"], got["tex"]) == (4, [{"file": os.path.join(d["proj"], "main.tex"), "lines": [9, 9]}])
    session.fake.set(primary="$ git status")
    assert data(mcp.call("get_selection")) == {"text": "$ git status", "source": "primary", "viewer": "okular",
                                               "path": d["main"], "page": 3}


# --- C3 ---

def test_c3_goto_moves_okular_and_returns_the_entry(session, mcp):
    s = session.start(session.docs["paper"])
    assert data(mcp.call("goto", {"page": 3})) == entry(s, session.docs["paper"], 3, 5, True)
    assert session.state(s) == (session.docs["paper"], 3, 5)
    assert listed(mcp)[0]["page"] == 3


def test_c3_out_of_range_does_not_move(session, mcp):
    s = session.start(session.docs["paper"])
    session.go(s, 2)
    assert message(mcp.call("goto", {"page": 99})) == "page 99 outside 1-5"
    assert message(mcp.call("goto", {"page": 0, "path": session.docs["paper"]})) == "page 0 outside 1-5"
    assert session.state(s)[1] == 2


def test_c3_no_such_file(session, mcp):
    session.start(session.docs["paper"])
    gone = os.path.join(session.docs["docs"], "gone.pdf")
    assert message(mcp.call("goto", {"page": 1, "path": gone})) == f"no such file: {gone}"


def test_c3_cannot_open_a_broken_pdf(session, mcp):
    """Okular shows a modal error dialog and does not answer openDocument until it is closed."""
    session.start(session.docs["paper"])
    t = time.monotonic()
    assert message(mcp.call("goto", {"page": 2, "path": session.docs["broken"]})) == \
        f"cannot open {session.docs['broken']}"
    assert time.monotonic() - t < 15


def test_c3_opens_a_closed_file_in_the_first_window(session, mcp):
    d = session.docs
    s = session.start(d["paper"])
    assert data(mcp.call("goto", {"page": 2, "path": d["other"]})) == entry(s, d["other"], 2, 5, True)
    assert session.state(s) == (d["other"], 2, 5)


def test_c3_a_symlink_to_the_shown_file_moves_it_without_reopening(session, mcp):
    d = session.docs
    s = session.start(d["paper"])
    assert data(mcp.call("goto", {"page": 4, "path": d["link"]})) == entry(s, d["paper"], 4, 5, True)
    assert session.state(s) == (d["paper"], 4, 5)


def test_c3_a_path_with_spaces_and_hash(session, mcp):
    d = session.docs
    s = session.start()
    assert data(mcp.call("goto", {"page": 2, "path": d["odd"]})) == entry(s, d["odd"], 2, 5, True)
    assert session.state(s) == (d["odd"], 2, 5)


def test_c3_nothing_open(session, mcp):
    assert message(mcp.call("goto", {"page": 1})) == NOT_RUNNING
    session.start()
    assert message(mcp.call("goto", {"page": 1})) == NO_DOCUMENT
