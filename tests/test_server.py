import asyncio
import re
import subprocess
import sys
from pathlib import Path

import pytest

from okular_mcp import selection, server, viewer

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "okular_mcp"


@pytest.fixture
def sel(monkeypatch):
    """Fake selection.read; records the sources asked for."""
    class Sel:
        text, reads = "", []

        def __call__(self, source="primary"):
            self.reads.append(source)
            return self.text
    s = Sel()
    s.reads = []
    monkeypatch.setattr(selection, "read", s)
    return s


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


def _structured(result):
    return result.structured_content["result"]


# --- C1 ---

def test_c1_viewer_state_returns_entries(two_windows, pdfs):
    assert server.viewer_state() == viewer.entries()
    assert [(e["path"], e["active"]) for e in _structured(_call("viewer_state", {}))] == \
        [(pdfs["a"], True), (pdfs["b"], False)]


def test_c1_error_reaches_caller_verbatim(no_okular):
    assert _tool_error("viewer_state", {}).startswith("no Okular window with a PDF open")


def test_c1_no_document_error_reaches_caller_verbatim(bus):
    bus({1: {"path": "", "page": 0, "pages": 0}}, {"org.kde.okular-1": 1})
    assert _tool_error("viewer_state", {}) == "no Okular window with a PDF open"


# --- C2 ---

def test_c2_primary_attributed_to_active_entry(two_windows, pdfs, sel):
    sel.text = "lazy dog"
    assert server.get_selection() == {"text": "lazy dog", "source": "primary", "viewer": "okular",
                                      "path": pdfs["a"], "page": 3}
    assert sel.reads == ["primary"]


def test_c2_never_reports_tex_even_with_a_synctex_file(two_windows, pdfs, sel, monkeypatch):
    """Okular reports no selection position, so there is nothing to map through SyncTeX."""
    base = pdfs["a"][:-len(".pdf")]
    for ext in (".synctex.gz", ".synctex", ".tex"):
        open(base + ext, "w").close()
    sel.text = "lazy dog"
    for source in ("primary", "clipboard"):
        assert "tex" not in server.get_selection(source)
    monkeypatch.setattr(selection, "read", lambda source="primary": "")
    assert "tex" not in server.get_selection()


def test_c2_primary_without_active_entry_is_unattributed_not_an_error(no_okular, sel):
    sel.text = "words"
    assert server.get_selection("primary") == {"text": "words", "source": "primary"}


def test_c2_primary_with_only_empty_windows_is_unattributed(bus, sel):
    bus({1: {"path": "", "page": 0, "pages": 0}}, {"org.kde.okular-1": 1})
    assert server.get_selection() == {"text": "", "source": "primary"}


def test_c2_clipboard_is_never_attributed(two_windows, sel):
    sel.text = "copied"
    assert server.get_selection("clipboard") == {"text": "copied", "source": "clipboard"}
    assert two_windows.calls == []


def test_c2_empty_selection_is_empty_text(two_windows, pdfs, sel):
    assert server.get_selection()["text"] == ""


def test_c2_bad_source_raises_before_reading(two_windows, sel):
    with pytest.raises(ValueError) as e:
        server.get_selection("secondary")
    assert str(e.value) == "source must be 'primary' or 'clipboard'"
    assert sel.reads == [] and two_windows.calls == []


def test_c2_bad_source_reaches_caller_verbatim(sel):
    assert _tool_error("get_selection", {"source": "secondary"}) == "source must be 'primary' or 'clipboard'"


def test_c2_selection_tool_error_reaches_caller_verbatim(monkeypatch):
    def read(source="primary"):
        raise selection.SelectionError("no clipboard tool found: install wl-clipboard (Wayland) or xclip/xsel (X11)")
    monkeypatch.setattr(selection, "read", read)
    assert _tool_error("get_selection", {}).startswith("no clipboard tool found")


def test_c2_through_call_tool(two_windows, pdfs, sel):
    sel.text = "x"
    assert _call("get_selection", {}).structured_content == {
        "text": "x", "source": "primary", "viewer": "okular", "path": pdfs["a"], "page": 3}


# --- C3 ---

def test_c3_goto_returns_entry(two_windows, pdfs):
    r = _call("goto", {"page": 2, "path": pdfs["b"]}).structured_content
    assert r == {"viewer": "okular", "window": "org.kde.okular-200", "path": pdfs["b"], "page": 2,
                 "pages": 4, "active": False}


def test_c3_out_of_range_reaches_caller_verbatim(two_windows, pdfs):
    assert _tool_error("goto", {"page": 9, "path": pdfs["b"]}) == "page 9 outside 1-4"
    assert not [m for m in two_windows.methods() if m[1] == "goToPage"]


def test_c3_no_such_file_reaches_caller_verbatim(two_windows, tmp_path):
    gone = str(tmp_path / "gone.pdf")
    assert _tool_error("goto", {"page": 1, "path": gone}) == f"no such file: {gone}"
    assert two_windows.calls == []


def test_c3_cannot_open_reaches_caller_verbatim(two_windows, pdfs):
    two_windows.docs[pdfs["c"]] = None
    assert _tool_error("goto", {"page": 1, "path": pdfs["c"]}) == f"cannot open {pdfs['c']}"


def test_c3_nothing_open_reaches_caller_as_c1(no_okular):
    assert _tool_error("goto", {"page": 1}).startswith("no Okular window with a PDF open")


# --- C4: three tools, no reload, no PDF library ---

def _tools():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_c4_registered_tools_are_exactly_the_contract_and_readme_table():
    readme = (ROOT / "README.md").read_text()
    documented = set(re.findall(r"^\| `(\w+)\(", readme, re.M))
    assert set(_tools()) == documented == {"viewer_state", "get_selection", "goto"}


def test_c4_no_reload_anywhere():
    assert not hasattr(viewer, "reload") and not hasattr(server, "_reload")
    for src in PACKAGE.glob("*.py"):
        assert "reload" not in src.read_text().lower(), src.name


def test_c4_okular_methods_called_only_read_open_and_goto():
    called = set()
    for src in PACKAGE.glob("*.py"):
        called |= set(re.findall(r'_okular\(\s*[\w.]+,\s*"(\w+)"', src.read_text()))
    assert called == {"currentDocument", "currentPage", "pages", "openDocument", "goToPage"}


def test_c4_nothing_imports_pymupdf():
    for src in PACKAGE.glob("*.py"):
        assert not re.search(r"\b(pymupdf|fitz)\b", src.read_text()), src.name
    assert "pymupdf" not in (ROOT / "pyproject.toml").read_text().lower()
    out = subprocess.run(
        [sys.executable, "-c", "import sys, okular_mcp.server, okular_mcp.hook; "
         "print(sorted(m for m in ('pymupdf', 'fitz') if m in sys.modules))"],
        capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "[]"


def test_c4_tool_annotation_hints():
    hints = {n: t.annotations.model_dump(by_alias=True) for n, t in _tools().items()}
    assert {n for n, h in hints.items() if h["readOnlyHint"]} == {"viewer_state", "get_selection"}
    assert hints["goto"]["readOnlyHint"] is False and hints["goto"]["destructiveHint"] is False


# --- the tool() decorator ---

def test_direct_calls_keep_original_exception_types(two_windows, pdfs):
    with pytest.raises(ValueError, match="page 9 outside 1-4"):
        server.goto(9, pdfs["b"])
    with pytest.raises(FileNotFoundError):
        server.goto(1, pdfs["b"] + ".gone")


def test_unexpected_exception_stays_generic(monkeypatch):
    """Only anticipated errors pass their text on; a crash does not."""
    def boom():
        raise ZeroDivisionError("secret")
    monkeypatch.setattr(viewer, "entries", boom)
    with pytest.raises(server.ToolError) as e:
        _call("viewer_state", {})
    assert "secret" not in str(e.value)
