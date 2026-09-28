import pymupdf
import pytest

from okular_mcp import pdf


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "s.pdf"
    doc = pymupdf.open()
    for n in (1, 2):
        pg = doc.new_page()
        pg.insert_text((72, 72), f"Page {n} first line of text.")
        pg.insert_text((72, 100), "The quick brown fox jumps over the lazy dog.")
    doc.save(path)
    doc.close()
    return str(path)


def test_page_text(sample):
    out = pdf.page_text(sample, 2)
    assert out.startswith("--- page 2 (2 lines) ---\n1: Page 2 first line") and "2: The quick" in out
    assert "Page 1" not in out
    assert "Page 1" in pdf.page_text(sample, 1, 2)
    assert pdf.page_text(sample, 1, lines="2-").splitlines()[1:] == ["2: The quick brown fox jumps over the lazy dog."]
    assert pdf.page_text(sample, 1, lines="-1").count("\n") == 1
    with pytest.raises(ValueError):
        pdf.page_text(sample, 3)
    with pytest.raises(ValueError):
        pdf.page_text(sample, 1, lines="x")


def test_context(tmp_path):
    path = tmp_path / "c.pdf"
    doc = pymupdf.open()
    pg = doc.new_page()
    y = 72
    for para in ("Intro para line one.\nIntro para line two.", "Body para says the fox\njumps high.", "Closing para here."):
        pg.insert_text((72, y), para)
        y += 60
    doc.save(path); doc.close()
    c = pdf.context(str(path), 1, "the  fox\njumps")
    assert c["paragraph"] == "Body para says the fox\njumps high."
    assert c["before"] == "Intro para line one.\nIntro para line two." and c["after"] == "Closing para here."
    assert c["lines"] == "3-4" and c["paragraph_lines"] == "3-4"
    assert pdf.context(str(path), 1, "fox", neighbours=0)["before"] == ""
    with pytest.raises(LookupError):
        pdf.context(str(path), 1, "not on this page at all")
    with pytest.raises(ValueError):
        pdf.context(str(path), 1, "  ")


def test_highlight_roundtrip(sample):
    r = pdf.add_highlight(sample, 1, "brown fox", note="check", author="tester")
    assert r["page"] == 1
    anns = pdf.annotations(sample)
    assert len(anns) == 1
    a = anns[0]
    assert a["type"] == "Highlight" and a["page"] == 1
    assert a["author"] == "tester" and a["note"] == "check"
    assert a["text"] == "brown fox"
    assert pdf.annotations(sample, page=2) == []


def test_highlight_missing(sample):
    with pytest.raises(LookupError):
        pdf.add_highlight(sample, 1, "no such words here")


def test_remove(sample):
    pdf.add_highlight(sample, 1, "quick brown")
    xref = pdf.add_highlight(sample, 2, "lazy dog")["xref"]
    assert len(pdf.annotations(sample)) == 2
    r = pdf.remove_annotation(sample, xref)
    assert r["page"] == 2 and r["type"] == "Highlight"
    left = pdf.annotations(sample)
    assert len(left) == 1 and left[0]["text"] == "quick brown"
    with pytest.raises(LookupError):
        pdf.remove_annotation(sample, xref)


def test_mark_styles_and_margin_note(sample):
    r = pdf.mark_text(sample, 1, "lazy dog", note="why lazy?", style="underline", note_in_margin=True)
    assert "note_xref" in r
    anns = {a["xref"]: a for a in pdf.annotations(sample)}
    mark, note = anns[r["xref"]], anns[r["note_xref"]]
    assert mark["type"] == "Underline" and mark["text"].startswith("lazy dog") and mark["note"] == ""
    assert note["type"] == "Text" and note["note"] == "why lazy?" and note["reply_to"] == r["xref"]
    with pytest.raises(ValueError):
        pdf.mark_text(sample, 1, "fox", style="bold")


def test_add_note_and_reply(sample):
    a = pdf.add_note(sample, 2, "page-level remark")
    b = pdf.add_note(sample, 2, "near the fox", near_text="brown fox")
    c = pdf.add_note(sample, None, "answering", reply_to=b["xref"])
    assert c["page"] == 2 and c["reply_to"] == b["xref"]
    anns = pdf.annotations(sample, page=2)
    assert [x["note"] for x in anns] == ["page-level remark", "near the fox", "answering"]
    with pytest.raises(LookupError):
        pdf.add_note(sample, None, "x", reply_to=999999)


def test_attachments(sample):
    md = "# Notes\n\n```mermaid\ngraph TD; A-->B;\n```\n"
    r = pdf.attach_file(sample, 1, "notes.md", md, note="summary", near_text="brown fox")
    assert r["bytes"] == len(md.encode())
    (a,) = pdf.annotations(sample)
    assert a["type"] == "FileAttachment" and a["file"] == "notes.md" and a["bytes"] == r["bytes"]
    back = pdf.read_attachment(sample, r["xref"])
    assert back["content"] == md and back["name"] == "notes.md" and back["note"] == "summary"
    r2 = pdf.attach_file(sample, None, "diagram.mmd", "graph LR; X-->Y;", replace_xref=r["xref"])
    assert r2["xref"] == r["xref"] and r2["page"] == 1
    back = pdf.read_attachment(sample, r["xref"])
    assert back["content"] == "graph LR; X-->Y;" and back["name"] == "diagram.mmd"
    h = pdf.mark_text(sample, 2, "lazy dog")
    with pytest.raises(ValueError):
        pdf.read_attachment(sample, h["xref"])


def test_colors(sample):
    assert pdf.parse_color(None, (1, 1, 1)) == (1, 1, 1)
    assert pdf.parse_color("Green", (0, 0, 0)) == pdf.COLORS["green"]
    assert pdf.parse_color("#ff0000", (0, 0, 0)) == (1.0, 0.0, 0.0)
    assert pdf.parse_color("0.5,0.25,0", (0, 0, 0)) == (0.5, 0.25, 0.0)
    for bad in ("chartreuse", "1,2,3", "#12345"):
        with pytest.raises(ValueError):
            pdf.parse_color(bad, (0, 0, 0))
    r = pdf.mark_text(sample, 1, "brown fox", color="yellow")
    yellow = [round(c, 2) for c in pdf.COLORS["yellow"]]
    assert r["color"] == yellow
    n = pdf.add_note(sample, 1, "n", color="#0000ff")
    u = pdf.mark_text(sample, 2, "brown fox", style="underline", color="0.5,0.25,0")
    by = {a["xref"]: a for a in pdf.annotations(sample)}
    assert by[r["xref"]]["color"] == yellow
    assert by[n["xref"]]["color"] == [0.0, 0.0, 1.0]
    assert by[u["xref"]]["color"] == [0.5, 0.25, 0.0]
    with pytest.raises(ValueError):
        pdf.mark_text(sample, 1, "fox", color="chartreuse")
