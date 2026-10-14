"""C4: pdf-reader-writer-mcp writes a mark into the PDF Okular shows; Okular reloads it
by itself and stays on the document and page okular-mcp reported."""

import os
import shutil
import time

import pytest

from mcpclient import Server, data, text

pytestmark = pytest.mark.integration


def reloads(session, service, pdf):
    """How often Okular has (re)opened ``pdf``: its debug line for each document it loads."""
    name = os.path.basename(pdf)
    log = session.log[service].read_text(errors="replace")
    return sum(1 for line in log.splitlines() if "Metadata file is now:" in line and line.endswith(f'.{name}.xml"'))


def test_c4_a_mark_by_pdf_reader_writer_reloads_okular_on_the_same_page(session, mcp, tmp_path):
    exe = shutil.which("pdf-reader-writer-mcp")
    if not exe:
        pytest.skip("pdf-reader-writer-mcp is not on PATH")
    pdf = str(tmp_path / "paper.pdf")
    shutil.copyfile(session.docs["main"], pdf)
    service = session.start(pdf)
    assert data(mcp.call("goto", {"page": 3}))["page"] == 3
    entry = data(mcp.call("viewer_state"))
    assert (entry["path"], entry["page"], entry["pages"]) == (pdf, 3, 5)
    loads, size = reloads(session, service, pdf), os.path.getsize(pdf)
    assert loads == 1

    with open(session.logs / "pdf-reader-writer-mcp.log", "w") as log:
        rw = Server(exe, env=session.env, errlog=log)
        try:
            mark = rw.call("mark_text", {"path": entry["path"], "page": entry["page"], "text": "charlie words"})
            assert not mark.is_error, text(mark)
            assert os.path.getsize(pdf) != size
            # Okular's file watcher reloads the document (bounded wait), on the same page.
            end = time.monotonic() + 15
            while reloads(session, service, pdf) == loads and time.monotonic() < end:
                time.sleep(0.05)
            assert reloads(session, service, pdf) == loads + 1, "Okular did not reload the PDF"
            states = []
            end = time.monotonic() + 1.5
            while time.monotonic() < end:
                states.append({k: data(mcp.call("viewer_state"))[k] for k in ("path", "page", "pages")})
                time.sleep(0.1)
            assert all(s == {"path": pdf, "page": 3, "pages": 5} for s in states), states
            notes = data(rw.call("annotations", {"path": entry["path"], "page": entry["page"]}))
            notes = notes if isinstance(notes, list) else [notes]
            assert [(n["type"], n["text"], n["page"]) for n in notes] == [("Highlight", "charlie words", 3)]
        finally:
            rw.close()
