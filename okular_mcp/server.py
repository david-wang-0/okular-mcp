"""The MCP server: the viewer-state contract (CONTRACT.md) for Okular, three tools."""

from __future__ import annotations

import functools
from typing import Any, Callable

try:  # mcp >= 2: FastMCP was renamed MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError

from mcp.types import ToolAnnotations

from . import selection, viewer

READ = ToolAnnotations(readOnlyHint=True)
NAVIGATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)

SOURCES = ("primary", "clipboard")

mcp = FastMCP(
    "okular",
    instructions=(
        "What the user is reading in the Okular PDF viewer. viewer_state lists the open "
        "documents (path, 1-based page, page count), the active one first; get_selection "
        "returns the text they selected with the mouse, with that document's path and page; "
        "goto shows them a page, only when they ask to be shown a place. To read page text, "
        "links, the outline or annotations, or to write marks, use the pdf-reader-writer "
        "server with the path and page reported here. Okular re-renders by itself when the "
        "file changes, but keeps unsaved annotations in memory and loses them then: ask the "
        "user to save in Okular (Ctrl+S) before anything writes to the PDF."
    ),
)


def tool(title: str, annotations: ToolAnnotations) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a tool whose anticipated failures reach the model as their message.

    mcp >= 2 shows the model only "Error executing tool <name>" for any exception
    but ToolError, which would hide "page 9 outside 1-4" or "no Okular window with a
    PDF open". ViewerError and SelectionError are RuntimeErrors, so they are covered.
    """
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except (ValueError, LookupError, OSError, RuntimeError) as e:
                raise ToolError(str(e)) from e
        mcp.tool(title=title, annotations=annotations)(wrapper)
        return fn  # direct (Python) callers keep the original exception types
    return register


@tool(title="Okular: open documents and pages", annotations=READ)
def viewer_state() -> list[dict[str, Any]]:
    """Which PDFs and pages Okular is showing, the active one first.

    One entry per Okular window with a document: {"viewer": "okular", "window", "path", "page" (1-based), "pages", "active"}; the first window with a document is the active one. Pass `path` and `page` to pdf-reader-writer to read or mark the page."""
    return viewer.entries()


@tool(title="Okular: mouse selection", annotations=READ)
def get_selection(source: str = "primary") -> dict[str, Any]:
    """Text the user has selected with the mouse, with the document and page.

    `source="primary"`: the system primary selection (text selected with the mouse), with `viewer`, `path` and `page` of the active Okular document when one is open. `source="clipboard"`: the clipboard, without a document. `text` is "" when nothing is selected."""
    if source not in SOURCES:
        raise ValueError("source must be 'primary' or 'clipboard'")
    result: dict[str, Any] = {"text": selection.read(source), "source": source}
    if source == "primary":
        try:
            e = viewer.active()
        except viewer.ViewerError:
            return result
        result.update(viewer=e["viewer"], path=e["path"], page=e["page"])
    return result


@tool(title="Okular: show a page to the user", annotations=NAVIGATE)
def goto(page: int, path: str | None = None) -> dict[str, Any]:
    """Show the user a page in Okular; only when they ask to be shown a place.

    Shows `page` (1-based) of `path` (default: the active document), opening the file in Okular first if no window has it; returns that document's entry after the move."""
    return viewer.goto(page, path)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
