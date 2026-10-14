"""Where a selection sits in the PDF, and its LaTeX source lines (CONTRACT.md C2 ``tex``, C7).

Okular reports no selection position, only the text (the system primary selection).
``locate`` finds the selected words in the page's text layer, read with poppler's
``pdftotext -bbox-layout`` (Okular's text layer is poppler's too, so the words are the
same), on the current page and the pages next to it. ``tex_lines`` maps a unique match
to source lines with TeX Live's ``synctex edit``. Both are optional extras: a missing
tool, a timeout or unexpected output gives None, never an exception.
"""

from __future__ import annotations

import html
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field

MAX_QUERIES = 8
"""``synctex edit`` points per selection: first word, last word, one per printed line between."""

_PAGE = re.compile(r"<page\b[^>]*>(.*?)</page>", re.S)
_LINE = re.compile(r"<line\b[^>]*>(.*?)</line>", re.S)
_WORD = re.compile(r'<word xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">(.*?)</word>', re.S)


@dataclass(frozen=True)
class Word:
    """One word of the text layer: PDF points from the page's top-left, ``line`` its printed line."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    line: int

    @property
    def key(self) -> str:
        return _norm(self.text)


@dataclass
class Match:
    """The page the selection was found on, how often, and the first match's words by
    printed line; ``layer`` is all the words of that page."""

    page: int
    count: int
    lines: list[list[Word]] = field(default_factory=list)
    layer: list[Word] = field(default_factory=list, repr=False)

    @property
    def words(self) -> list[Word]:
        return [w for line in self.lines for w in line]


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


class _Deadline:
    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        left = self.end - time.monotonic()
        if left <= 0:
            raise subprocess.TimeoutExpired("deadline", 0)
        return left


def _run_all(cmds: list[list[str]], deadline: _Deadline) -> list[str]:
    """Run ``cmds`` concurrently; their stdouts. Raises on a timeout (all are killed) or a missing tool."""
    procs = []
    try:
        for cmd in cmds:
            procs.append(subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, text=True))
        return [p.communicate(timeout=deadline.left())[0] for p in procs]
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()


# --- locate ---

def page_words(pdf: str, first: int, last: int, timeout: float) -> dict[int, list[Word]]:
    """The words of pages ``first``..``last`` (pdftotext clamps ``last``), one pdftotext call."""
    out, = _run_all([["pdftotext", "-f", str(first), "-l", str(last), "-bbox-layout", pdf, "-"]],
                    _Deadline(timeout))
    pages: dict[int, list[Word]] = {}
    for n, page in enumerate(_PAGE.findall(out), start=first):
        words = pages[n] = []
        for i, line in enumerate(_LINE.findall(page)):
            for x0, y0, x1, y1, text in _WORD.findall(line):
                words.append(Word(html.unescape(text), float(x0), float(y0), float(x1), float(y1), i))
    return pages


def _fits(token: str, word: str, first: bool, last: bool) -> bool:
    """A selection may start inside a word and end inside one (character-level selection)."""
    if first and last:
        return token in word
    if first:
        return word.endswith(token)
    if last:
        return word.startswith(token)
    return token == word


def _match_at(tokens: list[str], words: list[Word], i: int) -> int | None:
    """The end index of a match of ``tokens`` starting at ``words[i]``, or None.

    A word TeX hyphenated at a line end ("establish-", "ment") also matches the token
    the two make together, with or without the hyphen, should the selection have it.
    """
    j, n = i, len(tokens)
    for k, tok in enumerate(tokens):
        if j >= len(words):
            return None
        first, last = k == 0, k == n - 1
        w = words[j].key
        if _fits(tok, w, first, last):
            j += 1
            continue
        if j + 1 < len(words) and len(w) > 1 and w.endswith("-") and words[j + 1].line != words[j].line:
            nxt = words[j + 1].key
            joined = [s for s in (w + nxt, w[:-1] + nxt) if _fits(tok, s, first, last)]
            # A first token inside the second half alone is that word's own match.
            if joined and not (first and nxt.endswith(tok)):
                j += 2
                continue
        return None
    return j


def find(text: str, words: list[Word]) -> tuple[int, list[Word]]:
    """How often ``text`` occurs in ``words``, and the words of the first occurrence."""
    tokens = [_norm(t) for t in text.split()]
    if not tokens:
        return 0, []
    count, first = 0, []
    for i in range(len(words)):
        end = _match_at(tokens, words, i)
        if end is not None:
            count += 1
            if not first:
                first = words[i:end]
    return count, first


def _group(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for w in words:
        if lines and lines[-1][-1].line == w.line:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def locate(text: str, pdf: str, page: int, pages: int | None = None,
           timeout: float = 2.0) -> Match | None:
    """The page among ``page``, ``page + 1``, ``page - 1`` (in that order) whose text has
    the selection's words, with the match; None if none has them or anything fails."""
    if not text.strip() or page < 1:
        return None
    last = page + 1 if pages is None else min(page + 1, pages)
    try:
        layer = page_words(pdf, max(1, page - 1), last, timeout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for p in (page, page + 1, page - 1):
        count, words = find(text, layer.get(p, []))
        if count:
            return Match(p, count, _group(words), layer.get(p, []))
    return None


# --- tex_lines ---

def synctex_file(pdf: str) -> str | None:
    base = os.path.splitext(pdf)[0]
    for ext in (".synctex.gz", ".synctex"):
        if os.path.isfile(base + ext):
            return base + ext
    return None


def _results(out: str, keys: tuple[str, ...]) -> list[dict[str, str]]:
    """The records between ``SyncTeX result begin`` and ``end``; each starts at ``keys[0]``."""
    records: list[dict[str, str]] = []
    inside = False
    for line in out.splitlines():
        if line.startswith("SyncTeX result begin"):
            inside = True
        elif line.startswith("SyncTeX result end"):
            break
        elif inside and ":" in line:
            k, v = line.split(":", 1)
            if k == keys[0]:
                records.append({})
            if k in keys and records:
                records[-1][k] = v
    return records


def _source(path: str, pdf: str) -> str:
    return os.path.normpath(os.path.join(os.path.dirname(pdf), path))


def edit(out: str, pdf: str) -> tuple[str, int] | None:
    """The first result of ``synctex edit``: (absolute normalised file, line), line >= 1."""
    for r in _results(out, ("Input", "Line")):
        try:
            line = int(r.get("Line", ""))
        except ValueError:
            continue
        if r.get("Input") and line >= 1:
            return _source(r["Input"], pdf), line
    return None


def view(out: str) -> list[tuple[int, float, float]]:
    """The (page, x, y) of each ``synctex view`` result."""
    found = []
    for r in _results(out, ("Page", "x", "y")):
        try:
            found.append((int(r["Page"]), float(r["x"]), float(r["y"])))
        except (KeyError, ValueError):
            continue
    return found


def _whole(w: Word, layer: list[Word]) -> Word:
    """For a word TeX hyphenated at a line end ("establish-"), its second half on the next
    printed line: synctex edit maps the first half to the line before when the word
    starts a source line, and the second half right."""
    if w.text.endswith("-") and w in layer:
        i = layer.index(w)
        if i + 1 < len(layer) and layer[i + 1].line != w.line:
            return layer[i + 1]
    return w


def _points(match: Match) -> list[Word]:
    """First word, one word per printed line between (evenly thinned), last word."""
    lines = match.lines
    first, last = lines[0][0], lines[-1][-1]
    middle = [ws[len(ws) // 2] for ws in lines[1:-1]]
    room = MAX_QUERIES - (1 if first is last else 2)
    if len(middle) > room:
        middle = [middle[round(i * (len(middle) - 1) / max(room - 1, 1))] for i in range(room)]
    first, last = _whole(first, match.layer), _whole(last, match.layer)
    return [first] if first is last else [first, *middle, last]


def _edit_cmd(pdf: str, page: int, w: Word) -> list[str]:
    return ["synctex", "edit", "-o", f"{page}:{(w.x0 + w.x1) / 2:.2f}:{(w.y0 + w.y1) / 2:.2f}:{pdf}"]


def _starts_next_line(w: Word, page: int, records: list[tuple[int, float, float]]) -> bool:
    """pdfTeX tags the glue after a source line's first word with the line before, so
    ``synctex edit`` gives that word the previous line. It is that first word when the
    next line's first node on this printed line starts right at the word's end."""
    return any(p == page and w.y0 - 1 <= y <= w.y1 + 1 and (w.x0 + w.x1) / 2 <= x <= w.x1 + 1
               for p, x, y in records)


def _lines(file: str, texts: dict[str, list[str] | None]) -> list[str] | None:
    """The lines of ``file`` (None if unreadable), read once into ``texts``."""
    if file not in texts:
        try:
            with open(file, encoding="utf-8", errors="replace") as f:
                texts[file] = f.read().splitlines()
        except OSError:
            texts[file] = None
    return texts[file]


def _may_start(file: str, line: int, texts: dict[str, list[str] | None]) -> bool:
    """A word can start source line ``line`` only if it has text: ``synctex view`` of a
    blank line, or of one past the end of the file, gives the records of a line nearby."""
    lines = _lines(file, texts)
    return lines is None or (line <= len(lines) and bool(lines[line - 1].strip()))


def _nonblank(file: str, line: int, texts: dict[str, list[str] | None]) -> int:
    """``line``, or the nearest non-blank line before it when it is blank: the last word
    of a paragraph maps to the blank line that ends it."""
    lines = _lines(file, texts)
    if not lines or line > len(lines):
        return line
    n = line
    while n > 1 and not lines[n - 1].strip():
        n -= 1
    return n if lines[n - 1].strip() else line


def tex_lines(pdf: str, match: Match | None, timeout: float = 2.0) -> list[dict] | None:
    """C2 ``tex``: ``[{"file", "lines": [first, last]}]`` per source file in document order,
    for a unique match on a PDF with SyncTeX data; None otherwise or on any failure."""
    if match is None or match.count != 1 or not match.lines:
        return None
    pdf = os.path.abspath(pdf)
    if not synctex_file(pdf):
        return None
    texts: dict[str, list[str] | None] = {}
    try:
        deadline = _Deadline(timeout)
        points = _points(match)
        hits = [edit(out, pdf) for out in
                _run_all([_edit_cmd(pdf, match.page, w) for w in points], deadline)]
        # The first-word fix, for the two ends of the range (a no-op elsewhere).
        ends = {i for i in (0, len(points) - 1) if hits[i] and _may_start(hits[i][0], hits[i][1] + 1, texts)}
        checks = {i: ["synctex", "view", "-i", f"{hits[i][1] + 1}:0:{hits[i][0]}", "-o", pdf] for i in ends}
        for i, out in zip(checks, _run_all(list(checks.values()), deadline)):
            if _starts_next_line(points[i], match.page, view(out)):
                hits[i] = (hits[i][0], hits[i][1] + 1)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    files: dict[str, list[int]] = {}
    for hit in hits:
        if hit:
            files.setdefault(hit[0], []).append(_nonblank(hit[0], hit[1], texts))
    return [{"file": f, "lines": [min(ls), max(ls)]} for f, ls in files.items()] or None


def forward_page(tex: str, line: int, pdf: str, timeout: float = 5.0) -> int | None:
    """The page ``synctex view`` gives ``tex``:``line`` in ``pdf``; None without SyncTeX data,
    without a result, or on any failure."""
    pdf = os.path.abspath(pdf)
    if not synctex_file(pdf):
        return None
    try:
        out, = _run_all([["synctex", "view", "-i", f"{line}:0:{os.path.abspath(tex)}", "-o", pdf]],
                        _Deadline(timeout))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    pages = [p for p, _, _ in view(out) if p >= 1]
    return pages[0] if pages else None
