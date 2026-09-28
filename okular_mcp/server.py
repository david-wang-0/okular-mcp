"""The MCP server: eleven tools over the viewer, selection and pdf backends."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

try:  # mcp >= 2: FastMCP was renamed MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from . import pdf, selection, viewer

mcp = FastMCP(
    "okular",
    instructions=(
        "Okular PDF viewer bridge. viewer_state tells you what the user is reading; "
        "get_selection returns the text under their mouse and context the paragraph around it; annotations collects their "
        "highlights and notes from the PDF; mark_text, add_note and attach_file write marks, notes and files (e.g. Markdown, Mermaid) back. "
        "Okular keeps unsaved annotations in memory: ask the user to save (Ctrl+S) "
        "before reading annotations or writing one."
    ),
)


def _current(path: str | None, page: int | None) -> tuple[str, int]:
    if path and page:
        return path, page
    w = viewer.current()
    return path or w.path, page or w.page


def _reload(path: str) -> None:
    try:
        viewer.reload(path)
    except viewer.ViewerError:
        pass


@mcp.tool()
def viewer_state() -> list[dict[str, Any]]:
    """Open Okular windows: document path, current page (1-based) and page count."""
    return [asdict(w) for w in viewer.windows()]


@mcp.tool()
def get_selection(source: str = "primary") -> str:
    """Text currently selected with the mouse ('primary') or on the clipboard ('clipboard')."""
    return selection.read(source)


@mcp.tool()
def page_text(path: str | None = None, page: int | None = None, to: int | None = None,
              lines: str | None = None) -> str:
    """Numbered text lines of a page range (default: the page shown in Okular);
    `lines` such as "12-30" narrows to a window. Line numbers match `context`."""
    path, page = _current(path, page)
    return pdf.page_text(path, page, to, lines)


@mcp.tool()
def context(neighbours: int = 1, text: str | None = None, path: str | None = None,
            page: int | None = None) -> dict[str, Any]:
    """The paragraph around the user's current mouse selection (or `text`) on the page
    shown, with `neighbours` paragraphs before and after and the line numbers, so you
    can see what they are pointing at without reading the whole page."""
    path, page = _current(path, page)
    if text is None:
        text = selection.read("primary")
    return pdf.context(path, page, text, neighbours)


@mcp.tool()
def annotations(path: str | None = None, page: int | None = None) -> list[dict[str, Any]]:
    """All annotations in the PDF (default: the open document): page, type, author,
    highlighted text, note. Only annotations saved to the file are visible."""
    if not path:
        path = viewer.current().path
    return pdf.annotations(path, page)


@mcp.tool()
def mark_text(text: str, note: str | None = None, style: str = "highlight",
              note_in_margin: bool = False, color: str | None = None,
              path: str | None = None, page: int | None = None) -> dict[str, Any]:
    """Mark every occurrence of `text` on a page (default: the one shown) as highlight,
    underline, squiggly or strikeout, saved into the PDF under a distinct author; quote
    enough words to be unique. A note sits on the mark, or with note_in_margin on a
    comment icon in the margin replying to it (least intrusive). `color`: blue, yellow,
    green, orange, pink, purple, red, cyan, grey, #rrggbb or r,g,b in 0-1 (default light
    blue; red for strikeout). The user must save Okular first or their unsaved marks are
    lost."""
    path, page = _current(path, page)
    result = pdf.mark_text(path, page, text, note, style, note_in_margin, color=color)
    _reload(path)
    return result


@mcp.tool()
def add_note(note: str, near_text: str | None = None, reply_to: int | None = None,
             color: str | None = None, path: str | None = None,
             page: int | None = None) -> dict[str, Any]:
    """Comment icon in the margin: level with `near_text`, or as a threaded reply to the
    annotation `reply_to` (an xref from `annotations`, e.g. to answer the user's own
    note), else at the top of the page. `color` as in mark_text. Same save-Okular-first
    caveat as mark_text."""
    if not path:
        w = viewer.current()
        path, page = w.path, page or (None if reply_to else w.page)
    result = pdf.add_note(path, page, note, near_text, reply_to, color)
    _reload(path)
    return result


@mcp.tool()
def attach_file(name: str, content: str, note: str | None = None,
                near_text: str | None = None, replace_xref: int | None = None,
                color: str | None = None, path: str | None = None,
                page: int | None = None) -> dict[str, Any]:
    """Embed a text file (e.g. notes.md, diagram.mmd) as a paperclip annotation in the
    margin, level with `near_text` or at the top of the page; or replace the file of
    an existing attachment `replace_xref`. Okular saves it via the icon's context menu.
    Same save-Okular-first caveat as mark_text."""
    if not path:
        w = viewer.current()
        path, page = w.path, page or (None if replace_xref else w.page)
    result = pdf.attach_file(path, page, name, content, note, near_text, replace_xref, color)
    _reload(path)
    return result


@mcp.tool()
def read_attachment(xref: int, path: str | None = None) -> dict[str, Any]:
    """Contents of a file-attachment annotation (an xref from `annotations` with a
    `file` field), decoded as text."""
    if not path:
        path = viewer.current().path
    return pdf.read_attachment(path, xref)


@mcp.tool()
def remove_annotation(xref: int, path: str | None = None) -> dict[str, Any]:
    """Delete an annotation by the `xref` reported by `annotations` (default: the open
    document) and save. Same save-in-Okular-first caveat as mark_text."""
    if not path:
        path = viewer.current().path
    result = pdf.remove_annotation(path, xref)
    _reload(path)
    return result


@mcp.tool()
def goto(page: int, path: str | None = None) -> dict[str, Any]:
    """Jump Okular to `page` of `path` (default: current document; opens it if needed)."""
    return asdict(viewer.goto(page, path))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
