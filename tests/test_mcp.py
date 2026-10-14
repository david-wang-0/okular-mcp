"""The real ``okular-mcp`` server over stdio, driven by the ``mcp`` package's client.

The server runs as a process with a minimal environment, as MCP clients start it:
no DBUS_SESSION_BUS_ADDRESS or WAYLAND_DISPLAY, ``PATH`` holding only the fake
``qdbus``, ``wl-paste``, ``pdftotext`` and ``synctex`` (tests/fakes.py), and
``XDG_RUNTIME_DIR`` a temporary directory with ``bus`` and ``wayland-0`` in it (C5).
Nothing here talks to a real bus or starts Okular.
"""

import os
import re

import pytest

from fakes import FakeBin, entry_point
from mcpclient import Server, coverage_env, data, text

NO_DOCUMENT = "no Okular window with a PDF open"
PREFIX = re.compile(r"^Error executing tool \w+: ")


class World:
    pass


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = World()
    root = tmp_path_factory.mktemp("mcp")
    w.fake = FakeBin(root)
    w.rt = root / "rt"
    w.rt.mkdir(mode=0o700)
    (w.rt / "bus").touch()
    (w.rt / "wayland-0").touch()
    w.docs = root / "papers"
    w.docs.mkdir()
    for n in ("a", "b", "c", "a b#c"):
        (w.docs / f"{n}.pdf").write_bytes(b"%PDF-1.4\n")
    w.a, w.b, w.c, w.odd = (str(w.docs / f"{n}.pdf") for n in ("a", "b", "c", "a b#c"))
    cmd = entry_point("okular-mcp")
    w.log = open(root / "server.log", "w")
    w.server = Server(cmd[0], cmd[1:], env={"PATH": w.fake.path, "XDG_RUNTIME_DIR": str(w.rt),
                                             "HOME": str(root), **coverage_env()}, errlog=w.log)
    yield w
    w.server.close()
    w.log.close()


@pytest.fixture
def mcp(world):
    world.fake.reset()
    return world


def message(result):
    """The text of a tool error, after the SDK's "Error executing tool <name>: " prefix."""
    assert result.is_error
    t = text(result)
    rest = PREFIX.sub("", t)
    assert rest, f"only the SDK's prefix reached the client: {t!r}"
    return rest


def call(mcp, name, **args):
    return mcp.server.call(name, args)


# --- the tool list, the server's name and instructions ---

def test_c4_three_tools_with_titles_annotations_and_parameters(mcp):
    tools = mcp.server.tools()
    assert set(tools) == {"viewer_state", "get_selection", "goto"}  # no reload, no writing
    assert {n: t.title for n, t in tools.items()} == {
        "viewer_state": "Okular: open documents and pages",
        "get_selection": "Okular: mouse selection",
        "goto": "Okular: show a page to the user"}
    hints = {n: t.annotations for n, t in tools.items()}
    assert hints["viewer_state"].read_only_hint is True
    assert hints["get_selection"].read_only_hint is True
    assert (hints["goto"].read_only_hint, hints["goto"].destructive_hint, hints["goto"].idempotent_hint) == \
        (False, False, True)
    schema = {n: t.input_schema for n, t in tools.items()}
    assert not schema["viewer_state"].get("properties") and not schema["viewer_state"].get("required")
    assert schema["get_selection"]["properties"]["source"]["default"] == "primary"
    assert not schema["get_selection"].get("required")
    assert schema["goto"]["required"] == ["page"]
    assert set(schema["goto"]["properties"]) == {"page", "path"}
    assert all(t.description for t in tools.values())


def test_server_name_and_instructions(mcp):
    assert mcp.server.name == "okular"
    instructions = mcp.server.instructions or ""
    assert "pdf-reader-writer" in instructions
    assert "goto shows them a page, only when they ask" in instructions
    assert "Ctrl+S" in instructions


# --- C1 ---

def test_c1_errors_reach_the_client_as_their_message(mcp):
    assert message(call(mcp, "viewer_state")) == f"{NO_DOCUMENT} (Okular is not running on the session bus)"
    mcp.fake.windows((1, "", 0, 0))
    assert message(call(mcp, "viewer_state")) == NO_DOCUMENT
    mcp.fake.set(fail={"qdbus": "Could not connect to D-Bus server"})
    assert message(call(mcp, "viewer_state")) == (
        f"{NO_DOCUMENT} (qdbus  failed: Could not connect to D-Bus server)")


def test_c1_entries_as_json_and_structured_content(mcp):
    mcp.fake.windows((1, "", 0, 0), (100, mcp.a, 3, 10), (200, mcp.b, 1, 4))
    res = call(mcp, "viewer_state")
    expected = [{"viewer": "okular", "window": "org.kde.okular-100", "path": mcp.a, "page": 3, "pages": 10,
                 "active": True},
                {"viewer": "okular", "window": "org.kde.okular-200", "path": mcp.b, "page": 1, "pages": 4,
                 "active": False}]
    assert data(res) == expected  # one text block per entry
    assert res.structured_content == {"result": expected}


# --- C2 and C5 ---

def test_c2_primary_found_on_the_next_page_with_tex(mcp):
    mcp.fake.windows((100, mcp.a, 3, 10))
    open(mcp.a[:-4] + ".synctex.gz", "w").close()
    try:
        mcp.fake.set(primary="lazy dog", layer={"3": ["the first page"], "4": ["the quick fox", "over the lazy dog"]},
                     edit=[[4, 1, 2, "./sec/a.tex", 12], [4, 1, 3, "./sec/a.tex", 13]])
        res = call(mcp, "get_selection")
        tex = [{"file": str(mcp.docs / "sec" / "a.tex"), "lines": [12, 13]}]
        assert data(res) == {"text": "lazy dog", "source": "primary", "viewer": "okular", "path": mcp.a,
                             "page": 4, "tex": tex}
        assert res.structured_content["tex"] == tex
    finally:
        os.remove(mcp.a[:-4] + ".synctex.gz")
    assert [c["argv"] for c in mcp.fake.calls("wl-paste")] == [["--primary", "--no-newline"]]
    assert mcp.fake.calls("pdftotext")[0]["argv"] == ["-f", "2", "-l", "4", "-bbox-layout", mcp.a, "-"]


def test_c2_primary_not_on_these_pages_keeps_the_current_page_without_tex(mcp):
    mcp.fake.windows((100, mcp.a, 3, 10))
    mcp.fake.set(primary="$ git status", layer={"3": ["the lazy dog"]})
    assert data(call(mcp, "get_selection")) == {"text": "$ git status", "source": "primary", "viewer": "okular",
                                                "path": mcp.a, "page": 3}


def test_c2_clipboard_is_never_attributed_and_runs_no_qdbus(mcp):
    mcp.fake.windows((100, mcp.a, 3, 10))
    mcp.fake.set(clipboard="copied")
    assert data(call(mcp, "get_selection", source="clipboard")) == {"text": "copied", "source": "clipboard"}
    assert [c["argv"] for c in mcp.fake.calls()] == [["--no-newline"]]


def test_c2_nothing_selected_is_empty_text_not_an_error(mcp):
    assert data(call(mcp, "get_selection")) == {"text": "", "source": "primary"}


@pytest.mark.parametrize("source", ["secondary", "", "PRIMARY"])
def test_c2_bad_source_reaches_the_client_before_any_tool_runs(mcp, source):
    assert message(call(mcp, "get_selection", source=source)) == "source must be 'primary' or 'clipboard'"
    assert mcp.fake.calls() == []


def test_c2_selection_tool_failure_reaches_the_client(mcp):
    mcp.fake.set(fail={"wl-paste": "Can't open display"})
    assert message(call(mcp, "get_selection")) == "wl-paste --primary --no-newline failed: Can't open display"


def test_c5_tools_run_with_the_session_variables_filled_in(mcp):
    mcp.fake.windows((100, mcp.a, 3, 10))
    mcp.fake.set(primary="x")
    call(mcp, "get_selection")
    seen = {(c["tool"], c["env"]["DBUS_SESSION_BUS_ADDRESS"], c["env"]["WAYLAND_DISPLAY"]) for c in mcp.fake.calls()}
    assert seen == {(tool, f"unix:path={mcp.rt}/bus", "wayland-0") for tool in ("qdbus", "wl-paste", "pdftotext")}


# --- C3 ---

def test_c3_goto_moves_and_returns_the_entry(mcp):
    mcp.fake.windows((100, mcp.a, 3, 10), (200, mcp.b, 1, 4))
    res = call(mcp, "goto", page=7)
    assert data(res) == {"viewer": "okular", "window": "org.kde.okular-100", "path": mcp.a, "page": 7,
                         "pages": 10, "active": True}
    assert res.structured_content["page"] == 7
    assert data(call(mcp, "goto", page=2, path=mcp.b))["window"] == "org.kde.okular-200"
    procs = mcp.fake.current()["procs"]
    assert (procs["100"]["page"], procs["200"]["page"]) == (7, 2)
    assert "opened" not in mcp.fake.current()


def test_c3_goto_opens_a_closed_file_by_file_url(mcp):
    mcp.fake.windows((100, mcp.a, 3, 10), docs={mcp.odd: 5})
    assert data(call(mcp, "goto", page=4, path=mcp.odd)) == {
        "viewer": "okular", "window": "org.kde.okular-100", "path": mcp.odd, "page": 4, "pages": 5, "active": True}
    assert mcp.fake.current()["opened"] == ["file://" + str(mcp.docs) + "/a%20b%23c.pdf"]


def test_c3_errors_reach_the_client_as_their_message(mcp):
    mcp.fake.windows((100, mcp.a, 3, 10), docs={mcp.c: None})
    assert message(call(mcp, "goto", page=11)) == "page 11 outside 1-10"
    assert message(call(mcp, "goto", page=0, path=mcp.a)) == "page 0 outside 1-10"
    gone = str(mcp.docs / "gone.pdf")
    assert message(call(mcp, "goto", page=1, path=gone)) == f"no such file: {gone}"
    assert message(call(mcp, "goto", page=99, path=mcp.c)) == f"cannot open {mcp.c}"
    mcp.fake.windows((100, "", 0, 0))
    assert message(call(mcp, "goto", page=1)) == NO_DOCUMENT
    mcp.fake.windows()
    assert message(call(mcp, "goto", page=1, path=mcp.a)).startswith(NO_DOCUMENT)


def test_c3_bad_arguments_are_a_tool_error_naming_the_parameter(mcp):
    res = call(mcp, "goto", page="seven")
    assert res.is_error and "page" in text(res)
    assert mcp.fake.calls() == []


# --- C4 ---

def test_c4_the_pdf_is_never_written(mcp):
    mcp.fake.windows((100, mcp.a, 1, 10), docs={mcp.b: 4})
    mcp.fake.set(primary="w", clipboard="w")
    before = {p: (open(p, "rb").read(), os.stat(p).st_mtime_ns) for p in (mcp.a, mcp.b)}
    call(mcp, "viewer_state")
    call(mcp, "get_selection")
    call(mcp, "get_selection", source="clipboard")
    call(mcp, "goto", page=2)
    call(mcp, "goto", page=3, path=mcp.b)
    assert {p: (open(p, "rb").read(), os.stat(p).st_mtime_ns) for p in (mcp.a, mcp.b)} == before
    methods = {c["argv"][2].rsplit(".", 1)[1] for c in mcp.fake.calls("qdbus") if len(c["argv"]) > 2}
    assert methods <= {"currentDocument", "currentPage", "pages", "openDocument", "goToPage",
                       "GetConnectionUnixProcessID"}
