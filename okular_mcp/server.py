"""The MCP server: eight tools over the viewer, selection and pdf backends."""

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
        "selection returns the text under their mouse; annotations collects their "
        "highlights and notes from the PDF; mark_text and add_note write marks and notes back. "
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
def page_text(path: str | None = None, page: int | None = None, to: int | None = None) -> str:
    """Text of a page range. Defaults to the document and page shown in Okular."""
    path, page = _current(path, page)
    return pdf.page_text(path, page, to)


@mcp.tool()
def annotations(path: str | None = None, page: int | None = None) -> list[dict[str, Any]]:
    """All annotations in the PDF (default: the open document): page, type, author,
    highlighted text, note. Only annotations saved to the file are visible."""
    if not path:
        path = viewer.current().path
    return pdf.annotations(path, page)


@mcp.tool()
def mark_text(text: str, note: str | None = None, style: str = "highlight",
              note_in_margin: bool = False, path: str | None = None,
              page: int | None = None) -> dict[str, Any]:
    """Mark every occurrence of `text` on a page (default: the one shown) as highlight,
    underline, squiggly or strikeout, saved into the PDF with a distinct colour/author;
    quote enough words to be unique. A note sits on the
    mark, or with note_in_margin on a comment icon in the margin replying to it (least
    intrusive). The user must save Okular first or their unsaved marks are lost."""
    path, page = _current(path, page)
    result = pdf.mark_text(path, page, text, note, style, note_in_margin)
    _reload(path)
    return result


@mcp.tool()
def add_note(note: str, near_text: str | None = None, reply_to: int | None = None,
             path: str | None = None, page: int | None = None) -> dict[str, Any]:
    """Comment icon in the margin: level with `near_text`, or as a threaded reply to the
    annotation `reply_to` (an xref from `annotations`, e.g. to answer the user's own
    note), else at the top of the page. Same save-Okular-first caveat as mark_text."""
    if not path:
        w = viewer.current()
        path, page = w.path, page or (None if reply_to else w.page)
    result = pdf.add_note(path, page, note, near_text, reply_to)
    _reload(path)
    return result


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
