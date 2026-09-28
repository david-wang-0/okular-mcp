"""The MCP server: seven tools over the viewer, selection and pdf backends."""

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
        "highlights and notes from the PDF; add_highlight writes a highlight back. "
        "Okular keeps unsaved annotations in memory: ask the user to save (Ctrl+S) "
        "before reading annotations or writing one."
    ),
)


def _current(path: str | None, page: int | None) -> tuple[str, int]:
    if path and page:
        return path, page
    w = viewer.current()
    return path or w.path, page or w.page


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
def add_highlight(text: str, note: str | None = None, path: str | None = None,
                  page: int | None = None) -> dict[str, Any]:
    """Highlight `text` on a page (default: the one shown) with an optional note, saved
    into the PDF in a distinct colour/author. The user must save Okular first or
    their unsaved marks are lost on reload."""
    path, page = _current(path, page)
    result = pdf.add_highlight(path, page, text, note)
    try:
        viewer.reload(path)
    except viewer.ViewerError:
        pass
    return result


@mcp.tool()
def remove_annotation(xref: int, path: str | None = None) -> dict[str, Any]:
    """Delete an annotation by the `xref` reported by `annotations` (default: the open
    document) and save. Same save-in-Okular-first caveat as add_highlight."""
    if not path:
        path = viewer.current().path
    result = pdf.remove_annotation(path, xref)
    try:
        viewer.reload(path)
    except viewer.ViewerError:
        pass
    return result


@mcp.tool()
def goto(page: int, path: str | None = None) -> dict[str, Any]:
    """Jump Okular to `page` of `path` (default: current document; opens it if needed)."""
    return asdict(viewer.goto(page, path))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
