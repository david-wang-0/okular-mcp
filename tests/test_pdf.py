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
    assert "--- page 2 ---" in out and "Page 2 first line" in out
    assert "Page 1" not in out
    assert "Page 1" in pdf.page_text(sample, 1, 2)
    with pytest.raises(ValueError):
        pdf.page_text(sample, 3)


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
