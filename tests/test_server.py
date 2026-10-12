import asyncio
import re
from pathlib import Path

import pymupdf
import pytest

from okular_mcp import pdf, selection, server, viewer
from okular_mcp.viewer import ViewerError, ViewerWindow


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "s.pdf"
    doc = pymupdf.open()
    for n in (1, 2):
        pg = doc.new_page()
        pg.insert_text((72, 72), f"Page {n} first line of text.")
        pg.insert_text((72, 100), "The quick brown fox jumps over the lazy dog.")
    doc.save(path); doc.close()
    return str(path)


class Fake:
    """Stand-in for the viewer and selection backends; records what the tools ask."""

    def __init__(self, monkeypatch, path, page=1, sel="", fail=False):
        self.path, self.page, self.sel, self.fail = path, page, sel, fail
        self.reloads, self.reads, self.current_calls = [], [], 0
        self.reload_error = False
        monkeypatch.setattr(viewer, "current", self.current)
        monkeypatch.setattr(viewer, "windows", lambda: [self.current()])
        monkeypatch.setattr(viewer, "reload", self.reload)
        monkeypatch.setattr(viewer, "goto", lambda page, path=None: ViewerWindow("svc", 1, path or self.path, page, 2))
        monkeypatch.setattr(selection, "read", self.read)

    def current(self):
        self.current_calls += 1
        if self.fail:
            raise ViewerError("no Okular window")
        return ViewerWindow("org.kde.okular-1", 1, self.path, self.page, 2)

    def reload(self, path):
        self.reloads.append(path)
        if self.reload_error:
            raise ViewerError("reload failed")

    def read(self, source="primary"):
        self.reads.append(source)
        return self.sel


@pytest.fixture
def fake(monkeypatch, sample):
    return lambda **kw: Fake(monkeypatch, sample, **kw)


# --- _current ---

def test_current_explicit_path_and_page_skip_viewer(fake):
    f = fake(fail=True)
    assert server._current("/x.pdf", 4) == ("/x.pdf", 4)
    assert f.current_calls == 0


def test_current_fills_missing_from_viewer(fake, sample):
    fake(page=2)
    assert server._current(None, None) == (sample, 2)
    assert server._current("/x.pdf", None) == ("/x.pdf", 2)
    assert server._current(None, 5) == (sample, 5)


def test_current_without_viewer_raises(fake):
    fake(fail=True)
    with pytest.raises(ViewerError):
        server._current(None, None)


# --- read tools ---

def test_viewer_state_dict_keys(fake, sample):
    fake(page=2)
    assert server.viewer_state() == [{"service": "org.kde.okular-1", "pid": 1, "path": sample,
                                      "page": 2, "pages": 2, "viewer": "okular"}]


def test_page_text_defaults_to_shown_page(fake):
    fake(page=2)
    assert server.page_text().startswith("--- page 2 ")
    assert server.page_text(page=1).startswith("--- page 1 ")


def test_links_defaults_to_shown_page(fake, sample, monkeypatch):
    fake(page=2)
    seen = []
    monkeypatch.setattr(pdf, "links", lambda p, n: seen.append((p, n)) or [])
    server.links()
    server.links(page=1)
    assert seen == [(sample, 2), (sample, 1)]


def test_context_reads_primary_selection_when_no_text(fake):
    f = fake(page=2, sel="lazy dog")
    c = server.context()
    assert f.reads == ["primary"] and c["page"] == 2 and c["selection"] == "lazy dog" and c["lines"] == "2-2"


def test_context_with_text_does_not_read_selection(fake):
    f = fake()
    assert server.context(text="brown fox")["selection"] == "brown fox"
    assert f.reads == []


def test_outline_annotations_read_attachment_default_to_current_path(fake, sample):
    fake()
    x = pdf.attach_file(sample, 1, "n.md", "body")["xref"]
    assert server.outline() == []
    assert [a["xref"] for a in server.annotations()] == [x]
    assert server.read_attachment(x)["content"] == "body"


def test_goto_returns_window_dict(fake):
    fake()
    assert server.goto(2, "/x.pdf") == {"service": "svc", "pid": 1, "path": "/x.pdf", "page": 2,
                                        "pages": 2, "viewer": "okular"}


# --- get_selection ---

def test_get_selection_located_on_shown_page(fake, sample):
    f = fake(page=2, sel="lazy dog")
    assert server.get_selection() == {"text": "lazy dog", "source": "primary", "path": sample,
                                      "page": 2, "lines": "2-2"}
    assert f.reads == ["primary"]


def test_get_selection_not_on_shown_page_has_no_location(fake):
    fake(sel="words from another window")
    assert server.get_selection() == {"text": "words from another window", "source": "primary"}


def test_get_selection_empty(fake):
    fake(sel="")
    assert server.get_selection() == {"text": "", "source": "primary"}


def test_get_selection_survives_viewer_error(fake):
    f = fake(sel="lazy dog", fail=True)
    assert server.get_selection("clipboard") == {"text": "lazy dog", "source": "clipboard"}
    assert f.reads == ["clipboard"]


def test_get_selection_survives_missing_file(fake, tmp_path):
    f = fake(sel="lazy dog")
    f.path = str(tmp_path / "gone.pdf")
    assert server.get_selection() == {"text": "lazy dog", "source": "primary"}


# --- write tools: reload after write, reload errors swallowed ---

WRITES = {
    "mark_text": lambda: server.mark_text("brown fox"),
    "add_note": lambda: server.add_note("n"),
    "attach_file": lambda: server.attach_file("a.md", "x"),
}


@pytest.mark.parametrize("tool", WRITES)
@pytest.mark.parametrize("reload_error", [False, True])
def test_write_tools_reload_after_write(fake, sample, tool, reload_error):
    f = fake(page=2)
    f.reload_error = reload_error
    r = WRITES[tool]()
    assert r["page"] == 2 and f.reloads == [sample]
    assert [a["xref"] for a in pdf.annotations(sample)] == [r["xref"]]


@pytest.mark.parametrize("reload_error", [False, True])
def test_remove_annotation_reloads_after_write(fake, sample, reload_error):
    f = fake()
    f.reload_error = reload_error
    x = pdf.add_note(sample, 2, "n")["xref"]
    assert server.remove_annotation(x)["page"] == 2
    assert f.reloads == [sample] and pdf.annotations(sample) == []


def test_mark_text_passes_style_note_and_color(fake, sample):
    fake()
    r = server.mark_text("lazy dog", note="hm", style="underline", note_in_margin=True, color="green", page=2)
    assert r["page"] == 2 and r["style"] == "underline" and "note_xref" in r
    assert r["color"] == [round(c, 2) for c in pdf.COLORS["green"]]


def test_add_note_reply_finds_target_page_not_shown_page(fake, sample):
    fake(page=1)
    target = pdf.add_note(sample, 2, "question")["xref"]
    r = server.add_note("answer", reply_to=target)
    assert r["page"] == 2 and r["reply_to"] == target
    assert {a["xref"]: a["page"] for a in pdf.annotations(sample)}[r["xref"]] == 2


def test_add_note_near_text_on_shown_page(fake):
    fake(page=2)
    assert server.add_note("n", near_text="lazy dog")["page"] == 2


def test_add_note_explicit_path_without_page_requires_page(fake, sample):
    """With an explicit path, add_note does not borrow the shown page (unlike mark_text)."""
    f = fake(page=2)
    with pytest.raises(ValueError, match="page is required"):
        server.add_note("n", path=sample)
    assert f.current_calls == 0


def test_attach_file_replace_finds_target_page_not_shown_page(fake, sample):
    fake(page=1)
    x = pdf.attach_file(sample, 2, "a.md", "old")["xref"]
    r = server.attach_file("b.md", "new", replace_xref=x)
    assert r["page"] == 2 and r["xref"] == x
    assert pdf.read_attachment(sample, x)["content"] == "new"


# --- registration ---

def _tools():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_registered_tools_match_readme_table():
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    documented = set(re.findall(r"^\| `(\w+)\(", readme, re.M))
    assert set(_tools()) == documented == {
        "viewer_state", "get_selection", "page_text", "context", "links", "outline", "annotations",
        "mark_text", "add_note", "attach_file", "read_attachment", "remove_annotation", "goto"}


def test_tool_annotation_hints():
    hints = {n: t.annotations.model_dump(by_alias=True) for n, t in _tools().items()}
    read = {"viewer_state", "get_selection", "page_text", "context", "links", "outline",
            "annotations", "read_attachment"}
    assert {n for n, h in hints.items() if h["readOnlyHint"]} == read
    assert {n for n, h in hints.items() if h["destructiveHint"]} == {"remove_annotation"}
    for n in ("mark_text", "add_note", "attach_file", "goto"):
        assert hints[n]["readOnlyHint"] is False and hints[n]["destructiveHint"] is False


# --- errors reach the model verbatim, as ToolError (mcp >= 2 hides anything else) ---

def _call(name, args):
    return asyncio.run(server.mcp.call_tool(name, args))


def _tool_error(name, args):
    """The text the model sees. mcp 2.3 raises ToolError from call_tool, prefixed with
    "Error executing tool <name>: ", and turns it into an isError result with that text;
    a crash would be an UnexpectedToolError carrying the prefix alone."""
    with pytest.raises(server.ToolError) as e:
        _call(name, args)
    assert type(e.value) is server.ToolError  # not UnexpectedToolError
    prefix = f"Error executing tool {name}: "
    assert str(e.value).startswith(prefix)
    return str(e.value)[len(prefix):]


def test_viewer_error_reaches_caller_as_tool_error(fake):
    fake(fail=True)
    assert _tool_error("page_text", {}) == "no Okular window"


def test_pdf_value_error_reaches_caller_as_tool_error(fake, sample):
    fake()
    assert _tool_error("links", {"path": sample, "page": 9}) == "page 9 outside 1-2"


def test_selection_error_reaches_caller_as_tool_error(fake, monkeypatch):
    fake()
    monkeypatch.setattr(selection, "read", lambda source="primary": (_ for _ in ()).throw(
        selection.SelectionError("source must be 'primary' or 'clipboard'")))
    assert _tool_error("get_selection", {"source": "x"}) == "source must be 'primary' or 'clipboard'"


def test_missing_file_reaches_caller_as_tool_error(fake, tmp_path):
    fake()
    gone = str(tmp_path / "gone.pdf")
    assert gone in _tool_error("outline", {"path": gone})


def test_direct_calls_keep_original_exception_types(fake, sample):
    fake()
    with pytest.raises(ValueError, match="page 9 outside 1-2"):
        server.links(sample, 9)
