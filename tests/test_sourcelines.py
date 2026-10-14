"""sourcelines: the selection's page (C2 ``page``) and its SyncTeX source lines (C2 ``tex``).

Against fake pdftotext/synctex output (conftest ``tools``), plus one test with the real
tools on a compiled fixture, skipped when pdflatex, synctex or pdftotext is missing.
"""

import gzip
import os
import shutil
import subprocess
import time

import pytest

from conftest import box
from okular_mcp import sourcelines as sl

PAGES = {
    3: ["The quick brown fox jumps", "over the lazy dog and", "then runs away"],
    4: ["A second page with the", "quick brown fox again", "and a hyphen-", "ated word"],
    5: ["Page five has the ﬁnal oﬃce", "lazy dog & cat < mouse"],
}


@pytest.fixture
def pdf(tmp_path):
    """A PDF path with SyncTeX data next to it (the fake tools never read either)."""
    p = tmp_path / "paper.pdf"
    p.write_bytes(b"%PDF-1.4\n")
    (tmp_path / "paper.synctex.gz").write_bytes(gzip.compress(b"SyncTeX Version:1\n"))
    return str(p)


def texts(match):
    return [[w.text for w in line] for line in match.lines]


# --- locate: which page, how often, which words ---

def test_c2_locate_on_the_current_page(tools, pdf):
    t = tools(PAGES)
    m = sl.locate("lazy dog", pdf, 3, 9)
    assert (m.page, m.count, texts(m)) == (3, 1, [["lazy", "dog"]])
    assert t.calls == [[["pdftotext", "-f", "2", "-l", "4", "-bbox-layout", pdf, "-"]]]


def test_c2_locate_order_current_then_next_then_previous(tools, pdf):
    tools(PAGES)
    assert sl.locate("quick brown fox", pdf, 4).page == 4      # on 3 and 4: the current one
    assert sl.locate("quick brown fox", pdf, 3).page == 3
    assert sl.locate("lazy dog", pdf, 4).page == 5             # on 3 and 5: next before previous
    m = sl.locate("hyphen-", pdf, 5)
    assert m.page == 4


def test_c2_locate_only_within_one_page_of_the_current_one(tools, pdf):
    tools(PAGES)
    assert sl.locate("then runs away", pdf, 5) is None
    assert sl.locate("not there at all", pdf, 3) is None


def test_c2_locate_clamps_to_the_document(tools, pdf):
    t = tools(PAGES)
    sl.locate("x", pdf, 1, 1)
    assert t.calls[-1][0][1:5] == ["-f", "1", "-l", "1"]
    sl.locate("x", pdf, 5, 5)
    assert t.calls[-1][0][1:5] == ["-f", "4", "-l", "5"]


def test_c2_locate_counts_every_match(tools, pdf):
    tools({1: ["the cat and the cat", "and the cat"]})
    assert sl.locate("the cat", pdf, 1).count == 3
    assert sl.locate("cat and", pdf, 1).count == 2             # also across the line end
    assert sl.locate("the", pdf, 1).count == 3


def test_c2_locate_partial_first_and_last_words(tools, pdf):
    """A character-level selection may start inside a word and end inside one."""
    tools(PAGES)
    m = sl.locate("ick brown fox jumps\nov", pdf, 3)
    assert (m.count, texts(m)) == (1, [["quick", "brown", "fox", "jumps"], ["over"]])
    assert sl.locate("rown", pdf, 3).count == 1                # inside one word
    assert sl.locate("brown fo", pdf, 3).count == 1
    assert sl.locate("bro fox", pdf, 3) is None                # the first word must end the token
    assert sl.locate("quick rown", pdf, 3) is None             # inner words must be whole


def test_c2_locate_ignores_whitespace_case_and_compatibility_forms(tools, pdf):
    tools(PAGES)
    assert sl.locate("  OVER\tthe\n\nLazy  ", pdf, 3).count == 1
    assert sl.locate("the final office", pdf, 5).count == 1   # NFKC: ligatures ﬁ, ﬃ
    assert sl.locate("dog & cat < mouse", pdf, 5).count == 1  # entities in the XHTML


def test_c2_locate_across_a_hyphenated_line_break(tools, pdf):
    """poppler keeps "hyphen-" and "ated" as two words; the selection may join them."""
    tools(PAGES)
    for text in ("a hyphen- ated word", "a hyphen-\nated word", "a hyphen-ated word", "a hyphenated word",
                 "yphenated word", "a hyphenat"):
        m = sl.locate(text, pdf, 4)
        assert m and m.page == 4 and m.count == 1, text
    assert texts(sl.locate("a hyphenated", pdf, 4)) == [["a", "hyphen-"], ["ated"]]
    assert sl.locate("ated word", pdf, 4).count == 1           # not also counted from "hyphen-"
    tools({1: ["a hyphen- ated word"]})                         # on one printed line: two words
    assert sl.locate("hyphenated", pdf, 1) is None


def test_c2_locate_groups_words_by_printed_line(tools, pdf):
    tools(PAGES)
    m = sl.locate("fox jumps over the lazy dog and then", pdf, 3)
    assert texts(m) == [["fox", "jumps"], ["over", "the", "lazy", "dog", "and"], ["then"]]
    assert [w.line for w in m.words] == [0, 0, 1, 1, 1, 1, 1, 2]
    assert (m.words[0].x0, m.words[0].y0) == box(0, 3)[:2]


@pytest.mark.parametrize("text", ["", "  \n "])
def test_c2_locate_empty_selection_runs_nothing(tools, pdf, text):
    t = tools(PAGES)
    assert sl.locate(text, pdf, 3) is None and t.calls == []


@pytest.mark.parametrize("exc", [FileNotFoundError("pdftotext"), subprocess.TimeoutExpired("pdftotext", 1)])
def test_c2_locate_failure_is_none(tools, pdf, exc):
    t = tools(PAGES)
    t.fail["pdftotext"] = exc
    assert sl.locate("lazy dog", pdf, 3) is None


# --- tex_lines ---

MAIN, CHAP = "/doc/main.tex", "/doc/chapter.tex"
TEX_PAGE = {7: ["alpha beta gamma delta", "epsilon zeta eta theta", "iota kappa lambda mu",
                "nu xi omicron pi", "rho sigma tau upsilon"]}


def test_c2_tex_groups_per_file_in_document_order(tools, pdf):
    t = tools(TEX_PAGE, edit={(7, 0, 1): ("/doc/./chapter.tex", 12), (7, 1, 2): (MAIN, 30),
                              (7, 2, 2): (CHAP, 13), (7, 3, 2): (CHAP, 11), (7, 4, 2): (MAIN, 31)})
    m = sl.locate("beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau",
                  pdf, 7)
    assert sl.tex_lines(pdf, m) == [{"file": CHAP, "lines": [11, 13]}, {"file": MAIN, "lines": [30, 31]}]
    edits = [c for c in t.calls[1]]
    assert [c[:3] for c in edits] == [["synctex", "edit", "-o"]] * 5
    # first word, the middle word of each printed line between, last word
    assert [c[3].rsplit(":", 1)[0] for c in edits] == [
        f"7:{(box(i, j)[0] + box(i, j)[2]) / 2:.2f}:{(box(i, j)[1] + box(i, j)[3]) / 2:.2f}"
        for i, j in ((0, 1), (1, 2), (2, 2), (3, 2), (4, 2))]
    assert all(c[3].endswith(":" + pdf) for c in edits)


def test_c2_tex_relative_input_is_resolved_against_the_pdf(tools, pdf):
    base = os.path.dirname(pdf)
    tools(TEX_PAGE, edit={(7, 0, 0): ("./sec/../intro.tex", 4), (7, 0, 3): ("intro.tex", 6)})
    m = sl.locate("alpha beta gamma delta", pdf, 7)
    assert sl.tex_lines(pdf, m) == [{"file": os.path.join(base, "intro.tex"), "lines": [4, 6]}]


def test_c2_tex_single_word_and_single_line(tools, pdf):
    tools(TEX_PAGE, edit={(7, 2, 1): (MAIN, 9)})
    assert sl.tex_lines(pdf, sl.locate("kappa", pdf, 7)) == [{"file": MAIN, "lines": [9, 9]}]


def test_c2_tex_at_most_eight_queries(tools, pdf):
    pages = {1: [f"w{i}a w{i}b" for i in range(20)]}
    t = tools(pages, edit={(1, i, j): (MAIN, 100 + i) for i in range(20) for j in range(2)})
    m = sl.locate(" ".join(w for line in pages[1] for w in line.split()), pdf, 1)
    assert sl.tex_lines(pdf, m) == [{"file": MAIN, "lines": [100, 119]}]
    assert len(t.calls[1]) == sl.MAX_QUERIES == 8


def test_c2_tex_drops_lines_below_one_and_unmapped_points(tools, pdf):
    tools(TEX_PAGE, edit={(7, 0, 0): (MAIN, 0), (7, 1, 0): (MAIN, -1)})
    assert sl.tex_lines(pdf, sl.locate("alpha beta gamma delta epsilon", pdf, 7)) is None
    tools(TEX_PAGE, edit={(7, 1, 0): (MAIN, 5)})
    assert sl.tex_lines(pdf, sl.locate("alpha beta gamma delta epsilon", pdf, 7)) == [
        {"file": MAIN, "lines": [5, 5]}]


def test_c2_tex_first_word_of_a_source_line(tools, pdf):
    """synctex edit gives a source line's first word the line before; when the next
    line's first node on that printed line starts at the word's end, it is that line's."""
    x0, y0, x1, y1 = box(0, 1)
    t = tools(TEX_PAGE, edit={(7, 0, 1): (MAIN, 5), (7, 0, 3): (MAIN, 6)},
              views={(6, MAIN): [(7, x1, y1 - 2), (7, 400.0, 300.0)]})
    assert sl.tex_lines(pdf, sl.locate("beta gamma delta", pdf, 7)) == [{"file": MAIN, "lines": [6, 6]}]
    assert t.calls[-1] == [["synctex", "view", "-i", f"6:0:{MAIN}", "-o", pdf],
                           ["synctex", "view", "-i", f"7:0:{MAIN}", "-o", pdf]]


@pytest.mark.parametrize("source", ["Line one.\nThe first word.\n", "Line one.\nThe first word.\n\nNext.\n"])
def test_c2_tex_no_first_word_fix_onto_a_blank_or_missing_line(tools, pdf, tmp_path, source):
    """synctex view of a line with no text (past the end of an \\input file, or blank)
    gives a nearby line's records, which may start right at the word's end: that is no
    reason to move the word to that line."""
    tex = tmp_path / "part.tex"
    tex.write_text(source)
    x0, y0, x1, y1 = box(0, 0)
    t = tools(TEX_PAGE, edit={(7, 0, 0): (str(tex), 2)}, views={(3, str(tex)): [(7, x1, y1 - 2)]})
    assert sl.tex_lines(pdf, sl.locate("alpha", pdf, 7)) == [{"file": str(tex), "lines": [2, 2]}]
    assert not [c for batch in t.calls for c in batch if c[:2] == ["synctex", "view"]]
    tex.write_text("Line one.\nThe first word.\nThe next line.\n")  # with text there, the fix applies
    assert sl.tex_lines(pdf, sl.locate("alpha", pdf, 7)) == [{"file": str(tex), "lines": [3, 3]}]


def test_c2_tex_last_word_of_a_selection_that_starts_a_source_line(tools, pdf):
    x0, y0, x1, y1 = box(0, 3)
    tools(TEX_PAGE, edit={(7, 0, 0): (MAIN, 5), (7, 0, 3): (MAIN, 5)},
          views={(6, MAIN): [(7, x1, y0 + 8)]})
    assert sl.tex_lines(pdf, sl.locate("alpha beta gamma delta", pdf, 7)) == [{"file": MAIN, "lines": [5, 6]}]


@pytest.mark.parametrize("where", [
    "start of the word",          # the next line starts at this word's start: synctex has it right
    "next printed line",
    "other page",
])
def test_c2_tex_last_word_of_a_source_line_stays(tools, pdf, where):
    x0, y0, x1, y1 = box(0, 1)
    rec = {"start of the word": (7, x0, y1 - 2), "next printed line": (7, x1, y1 + 18),
           "other page": (8, x1, y1 - 2)}[where]
    tools(TEX_PAGE, edit={(7, 0, 1): (MAIN, 5)}, views={(6, MAIN): [rec]})
    assert sl.tex_lines(pdf, sl.locate("beta", pdf, 7)) == [{"file": MAIN, "lines": [5, 5]}]


def test_c2_tex_hyphenated_end_word_is_queried_at_its_second_half(tools, pdf):
    t = tools({2: ["one two hyphen-", "ated three four"]},
              edit={(2, 0, 2): (MAIN, 4), (2, 1, 0): (MAIN, 5), (2, 1, 1): (MAIN, 5)})
    assert sl.tex_lines(pdf, sl.locate("hyphen-\nated three", pdf, 2)) == [{"file": MAIN, "lines": [5, 5]}]
    assert [c[3].split(":")[1] for c in t.calls[1]] == [f"{(box(1, 0)[0] + box(1, 0)[2]) / 2:.2f}",
                                                        f"{(box(1, 1)[0] + box(1, 1)[2]) / 2:.2f}"]
    assert sl.tex_lines(pdf, sl.locate("one two hyph", pdf, 2)) == [{"file": MAIN, "lines": [5, 5]}]


def test_c2_tex_blank_line_maps_to_the_line_before(tools, pdf, tmp_path):
    """The last word of a paragraph maps to the blank line that ends it."""
    tex = tmp_path / "body.tex"
    tex.write_text("first\nsecond\n\n  \nthird\n")
    tools(TEX_PAGE, edit={(7, 0, 0): (str(tex), 2), (7, 0, 1): (str(tex), 4), (7, 0, 2): (str(tex), 5),
                          (7, 0, 3): (str(tex), 9)})
    assert sl.tex_lines(pdf, sl.locate("alpha beta", pdf, 7)) == [{"file": str(tex), "lines": [2, 2]}]
    assert sl.tex_lines(pdf, sl.locate("beta gamma", pdf, 7)) == [{"file": str(tex), "lines": [2, 5]}]
    assert sl.tex_lines(pdf, sl.locate("gamma delta", pdf, 7)) == [{"file": str(tex), "lines": [5, 9]}]


def test_c2_tex_only_for_a_unique_match(tools, pdf):
    t = tools({1: ["same words here", "same words here"]}, edit={(1, 0, 0): (MAIN, 1), (1, 1, 0): (MAIN, 2)})
    m = sl.locate("same words", pdf, 1)
    assert m.count == 2
    assert sl.tex_lines(pdf, m) is None and len(t.calls) == 1
    assert sl.tex_lines(pdf, None) is None


def test_c2_tex_needs_synctex_data_next_to_the_pdf(tools, pdf, tmp_path):
    t = tools(TEX_PAGE, edit={(7, 0, 0): (MAIN, 1)})
    m = sl.locate("alpha", pdf, 7)
    os.remove(tmp_path / "paper.synctex.gz")
    assert sl.tex_lines(pdf, m) is None and len(t.calls) == 1
    (tmp_path / "paper.synctex").write_text("SyncTeX Version:1\n")
    assert sl.tex_lines(pdf, m) == [{"file": MAIN, "lines": [1, 1]}]


@pytest.mark.parametrize("exc", [FileNotFoundError("synctex"), subprocess.TimeoutExpired("synctex", 1)])
def test_c2_tex_failure_is_none(tools, pdf, exc):
    t = tools(TEX_PAGE, edit={(7, 0, 0): (MAIN, 1)})
    m = sl.locate("alpha", pdf, 7)
    t.fail["synctex"] = exc
    assert sl.tex_lines(pdf, m) is None


def test_parsers_tolerate_junk():
    assert sl.edit("SyncTeX result begin\nInput:/a.tex\nLine:x\nInput:/b.tex\nLine:3\nSyncTeX result end\n",
                   "/d/p.pdf") == ("/b.tex", 3)
    assert sl.edit("Input:/a.tex\nLine:3\n", "/d/p.pdf") is None  # outside the result block
    assert sl.view("SyncTeX result begin\nPage:2\nx:1.5\ny:oops\nPage:3\nx:1\ny:2\nSyncTeX result end") == [(3, 1.0, 2.0)]


# --- _run_all: concurrency and the deadline, with real processes ---

@pytest.mark.skipif(not shutil.which("sleep"), reason="needs sleep")
def test_run_all_kills_everything_at_the_deadline():
    t = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        sl._run_all([["sleep", "5"], ["sleep", "5"]], sl._Deadline(0.2))
    assert time.monotonic() - t < 1


def test_run_all_runs_concurrently():
    t = time.monotonic()
    outs = sl._run_all([["sh", "-c", "sleep 0.3; echo a"], ["sh", "-c", "sleep 0.3; echo b"]], sl._Deadline(5))
    assert outs == ["a\n", "b\n"] and time.monotonic() - t < 0.55


# --- forward_page (okular-mcp-open) ---

def test_forward_page(tools, pdf):
    t = tools(views={(12, MAIN): [(3, 1.0, 2.0), (4, 1.0, 2.0)]})
    assert sl.forward_page(MAIN, 12, pdf) == 3
    assert t.calls == [[["synctex", "view", "-i", f"12:0:{MAIN}", "-o", pdf]]]
    assert sl.forward_page(MAIN, 13, pdf) is None


def test_forward_page_without_synctex_data(tools, pdf, tmp_path):
    t = tools(views={(12, MAIN): [(3, 1.0, 2.0)]})
    os.remove(tmp_path / "paper.synctex.gz")
    assert sl.forward_page(MAIN, 12, pdf) is None and t.calls == []


# --- the real tools on a compiled fixture ---

MAIN_TEX = r"""\documentclass{article}
\usepackage[a6paper]{geometry}
\begin{document}
Alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike
november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu.
Second source line starts here with plenty of words to wrap across the printed
line boundary and keep going for a while longer than one printed line in this tiny page.

\input{chapter}

\newpage
Final page text with a distinctive marker phrase zebracorn appears here only once.
\end{document}
"""

CHAPTER_TEX = r"""Chapter paragraph begins with unmistakable wording about quasihyphenation
antidisestablishmentarianism supercalifragilisticexpialidocious incomprehensibilities
counterrevolutionaries and other extraordinarily long words to force hyphenation
somewhere in this narrow column of the page.

\newpage
Page two marker words duplicated nowhere else in this document at all.
"""


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    if not all(shutil.which(t) for t in ("pdflatex", "synctex", "pdftotext")):
        pytest.skip("needs pdflatex, synctex and pdftotext")
    d = tmp_path_factory.mktemp("latex")
    (d / "main.tex").write_text(MAIN_TEX)
    (d / "chapter.tex").write_text(CHAPTER_TEX)
    subprocess.run(["pdflatex", "-synctex=1", "-interaction=batchmode", "main.tex"], cwd=d,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    if not (d / "main.pdf").exists():
        pytest.skip("pdflatex failed")
    return str(d / "main.pdf"), str(d / "main.tex"), str(d / "chapter.tex")


@pytest.mark.parametrize("text, page, current, tex", [
    # starts with the first word of main.tex line 6, which synctex edit gives line 5
    ("Second source line starts", 1, 1, [("main", 6, 6)]),
    ("zulu. Second", 1, 1, [("main", 5, 6)]),
    ("ankee zulu.", 1, 1, [("main", 5, 5)]),
    ("mike november osc", 1, 2, [("main", 4, 5)]),
    ("going for a while longer than one printed line in this tiny page. Chapter paragraph begins",
     1, 1, [("main", 7, 7), ("chapter", 1, 1)]),
    ("counterrevolutionaries and other", 1, 1, [("chapter", 3, 3)]),
    ("antidisestablish-\nmentarianism", 1, 1, [("chapter", 2, 2)]),
    ("antidisestablishmentarianism supercali", 1, 1, [("chapter", 2, 2)]),
    ("Page two marker words", 2, 3, [("chapter", 7, 7)]),
    ("distinctive marker phrase", 3, 2, [("main", 12, 12)]),
])
def test_c2_real_tools(compiled, text, page, current, tex):
    pdf, main, chapter = compiled
    files = {"main": main, "chapter": chapter}
    m = sl.locate(text, pdf, current, 3)
    assert m and (m.page, m.count) == (page, 1)
    assert sl.tex_lines(pdf, m) == [{"file": files[f], "lines": [a, b]} for f, a, b in tex]


def test_c2_real_tools_first_word_quirk_is_real(compiled):
    """The fix above is needed: synctex edit alone gives "Second" (main.tex:6) line 5."""
    pdf, main, _ = compiled
    m = sl.locate("Second", pdf, 1, 3)
    w = m.words[0]
    out = subprocess.run(sl._edit_cmd(pdf, 1, w), capture_output=True, text=True).stdout
    assert sl.edit(out, pdf) == (main, 5)
    assert sl.tex_lines(pdf, m) == [{"file": main, "lines": [6, 6]}]


def test_c2_real_tools_first_word_of_the_last_line_of_an_input_file(tmp_path):
    """The paragraph that ends an \\input file: its first word stays on its own line
    (synctex view of the line after the file's end points right after that word)."""
    if not all(shutil.which(t) for t in ("pdflatex", "synctex", "pdftotext")):
        pytest.skip("needs pdflatex, synctex and pdftotext")
    (tmp_path / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\n\\input{part}\n\\end{document}\n")
    (tmp_path / "part.tex").write_text("Page charlie words here.\n\nThe quick brown fox jumps over the lazy dog.\n")
    subprocess.run(["pdflatex", "-synctex=1", "-interaction=batchmode", "main.tex"], cwd=tmp_path,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    pdf, part = str(tmp_path / "main.pdf"), str(tmp_path / "part.tex")
    for text in ("The quick brown fox", "The quick brown fox jumps over the lazy dog."):
        assert sl.tex_lines(pdf, sl.locate(text, pdf, 1, 1)) == [{"file": part, "lines": [3, 3]}], text


def test_real_forward_page(compiled):
    pdf, main, chapter = compiled
    assert sl.forward_page(chapter, 7, pdf) == 2
    assert sl.forward_page(main, 12, pdf) == 3
    assert sl.forward_page(os.path.join(os.path.dirname(pdf), "nothere.tex"), 1, pdf) is None


# --- deadlines and the rarer inputs ---

def test_deadline_in_the_past_raises_and_kills_what_it_started(tmp_path, monkeypatch):
    """A tool still running at the deadline is killed, and locate gives None."""
    from fakes import FakeBin
    fake = FakeBin(tmp_path / "fake", tools=("pdftotext",))
    fake.set(slow={"pdftotext": 30})
    monkeypatch.setenv("PATH", fake.path)
    with pytest.raises(subprocess.TimeoutExpired):
        sl._Deadline(0).left()
    started = []
    real = subprocess.Popen

    def popen(*a, **kw):
        started.append(real(*a, **kw))
        return started[-1]
    monkeypatch.setattr(sl.subprocess, "Popen", popen)
    t = time.monotonic()
    assert sl.locate("words", "/x.pdf", 1, timeout=0.2) is None
    assert time.monotonic() - t < 5
    assert started and all(p.poll() is not None for p in started)


@pytest.mark.parametrize("text", ["", "  \n\t"])
def test_find_without_words_finds_nothing(text):
    assert sl.find(text, [sl.Word("a", 0, 0, 1, 1, 0)]) == (0, [])


def test_whole_keeps_a_hyphenated_word_without_a_second_half():
    """At the end of the page, or followed on its own printed line, "word-" is its own point."""
    end = sl.Word("estab-", 0, 0, 1, 1, 0)
    same_line = [sl.Word("pre-", 0, 0, 1, 1, 0), sl.Word("x", 2, 0, 3, 1, 0)]
    assert sl._whole(end, [end]) is end
    assert sl._whole(same_line[0], same_line) is same_line[0]
    assert sl._whole(end, []) is end


def test_forward_page_failure_is_none(tools, pdf):
    t = tools(views={(3, "/x/a.tex"): [(2, 1.0, 1.0)]})
    t.fail["synctex"] = FileNotFoundError("synctex")
    assert sl.forward_page("/x/a.tex", 3, pdf) is None
