"""PDF text and annotations via PyMuPDF.

The PDF file is the shared state: Okular saves its annotations as standard PDF
annots, and everything here reads and writes those same annots, so both sides
interoperate through the file with no private format.
"""

from __future__ import annotations

import os
from typing import Any

import pymupdf

DEFAULT_AUTHOR = os.environ.get("OKULAR_MCP_AUTHOR", "llm")
# Light blue, so machine highlights are told apart from Okular's default yellow.
DEFAULT_COLOR = (0.6, 0.8, 1.0)

_MARKUP = {
    pymupdf.PDF_ANNOT_HIGHLIGHT,
    pymupdf.PDF_ANNOT_UNDERLINE,
    pymupdf.PDF_ANNOT_SQUIGGLY,
    pymupdf.PDF_ANNOT_STRIKE_OUT,
}


def _open(path: str) -> pymupdf.Document:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return pymupdf.open(path)


def page_text(path: str, page: int, to: int | None = None) -> str:
    """Text of pages ``page``..``to`` (1-based, inclusive), pages separated by a marker."""
    with _open(path) as doc:
        last = to or page
        if not 1 <= page <= last <= doc.page_count:
            raise ValueError(f"page range {page}-{last} outside 1-{doc.page_count}")
        parts = []
        for n in range(page, last + 1):
            parts.append(f"--- page {n} ---\n{doc[n - 1].get_text()}")
        return "\n".join(parts)


def _marked_text(pg: pymupdf.Page, annot: pymupdf.Annot) -> str:
    """Words of the page whose centre line lies inside one of the annot's quads.

    Quads follow the layout, so a highlight over a line break is several quads
    and one word can be split across two; collecting whole words by index avoids
    partial or duplicated words.
    """
    verts = annot.vertices or []
    quads = [pymupdf.Quad(verts[i:i + 4]).rect for i in range(0, len(verts) - 3, 4)] or [annot.rect]
    words = pg.get_text("words")  # (x0, y0, x1, y1, word, block, line, wordno)
    picked: list[int] = []
    for q in quads:
        for i, w in enumerate(words):
            r = pymupdf.Rect(w[:4])
            ymid = (r.y0 + r.y1) / 2
            if q.y0 <= ymid <= q.y1 and min(r.x1, q.x1) - max(r.x0, q.x0) > 0.3 * r.width:
                if i not in picked:
                    picked.append(i)
    return " ".join(words[i][4] for i in sorted(picked))


def annotations(path: str, page: int | None = None) -> list[dict[str, Any]]:
    """Every annotation, with the text under markup annots and the note contents."""
    out: list[dict[str, Any]] = []
    with _open(path) as doc:
        pages = [page] if page else range(1, doc.page_count + 1)
        for n in pages:
            pg = doc[n - 1]
            for annot in pg.annots():
                kind, name = annot.type[0], annot.type[1]
                info = annot.info
                entry: dict[str, Any] = {
                    "page": n,
                    "type": name,
                    "author": info.get("title", ""),
                    "note": info.get("content", ""),
                    "modified": info.get("modDate", ""),
                    "xref": annot.xref,
                }
                if kind in _MARKUP:
                    entry["text"] = _marked_text(pg, annot)
                if annot.colors.get("stroke"):
                    entry["color"] = [round(c, 2) for c in annot.colors["stroke"]]
                out.append(entry)
    return out


def add_highlight(path: str, page: int, text: str, note: str | None = None,
                  author: str = DEFAULT_AUTHOR,
                  color: tuple[float, float, float] = DEFAULT_COLOR) -> dict[str, Any]:
    """Highlight the first occurrence of ``text`` on ``page`` and save incrementally."""
    doc = _open(path)
    try:
        if not 1 <= page <= doc.page_count:
            raise ValueError(f"page {page} outside 1-{doc.page_count}")
        pg = doc[page - 1]
        quads = pg.search_for(text, quads=True)
        if not quads:
            # Retry with whitespace normalised: selections often carry line breaks.
            quads = pg.search_for(" ".join(text.split()), quads=True)
        if not quads:
            raise LookupError(f"text not found on page {page}: {text[:60]!r}")
        annot = pg.add_highlight_annot(quads)
        annot.set_colors(stroke=color)
        now = pymupdf.get_pdf_now()
        annot.set_info(title=author, content=note or "", creationDate=now, modDate=now)
        annot.update()
        if not doc.can_save_incrementally():
            raise RuntimeError("document cannot be saved incrementally (encrypted or repaired?)")
        doc.saveIncr()
        return {"page": page, "xref": annot.xref, "text": text, "note": note or "", "author": author}
    finally:
        doc.close()


def remove_annotation(path: str, xref: int, page: int | None = None) -> dict[str, Any]:
    """Delete the annotation with PDF object number ``xref`` and save incrementally."""
    doc = _open(path)
    try:
        pages = [page] if page else range(1, doc.page_count + 1)
        for n in pages:
            pg = doc[n - 1]
            for annot in pg.annots():
                if annot.xref == xref:
                    info = {"page": n, "xref": xref, "type": annot.type[1],
                            "author": annot.info.get("title", "")}
                    pg.delete_annot(annot)
                    doc.saveIncr()
                    return info
        raise LookupError(f"no annotation with xref {xref}")
    finally:
        doc.close()
