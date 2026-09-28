# okular-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an LLM session read
and annotate a PDF **alongside you in [Okular](https://okular.kde.org/)**: it
sees which document and page you are on, the text under your mouse, and the
highlights and notes you have made, and it can highlight passages back and
drive the viewer to a page.

The PDF file is the shared state. Okular saves its annotations as standard PDF
annots; this server reads and writes those same annots with
[PyMuPDF](https://pymupdf.readthedocs.io/), so nothing lives in a private
database and the notes travel with the file.

## Tools

| Tool | What it does |
|---|---|
| `viewer_state()` | Every open Okular window: document path, current page (1-based), page count |
| `get_selection(source="primary")` | Text currently selected with the mouse (`primary`), or the clipboard (`clipboard`) |
| `page_text(path?, page?, to?)` | Text of a page range; defaults to the page shown in Okular |
| `annotations(path?, page?)` | All saved annotations: page, type, author, highlighted text, note |
| `mark_text(text, note?, style?, note_in_margin?, path?, page?)` | Mark every occurrence of `text` on the page (quote enough to be unique) as `highlight` (default), `underline`, `squiggly` or `strikeout`, saved incrementally into the PDF. The note sits on the mark, or with `note_in_margin` on a comment icon in the margin that replies to the mark |
| `add_note(note, near_text?, reply_to?, path?, page?)` | Comment icon in the margin: level with `near_text`, as a threaded reply to annotation `reply_to`, or at the top of the page |
| `remove_annotation(xref, path?)` | Delete an annotation by the `xref` that `annotations` reports, saved incrementally |
| `goto(page, path?)` | Jump Okular to a page, opening the document first if needed |

Marks written by the server carry a distinct author (`llm`, override with
`OKULAR_MCP_AUTHOR`) and a light-blue colour (blue for underlines and notes, red for strikeout), so they
are told apart from yours.

## Requirements

Linux with KDE's Okular on the session D-Bus, plus:

- `qdbus` (Qt tools; installed with Okular on most distributions)
- `wl-clipboard` on Wayland, or `xclip`/`xsel` on X11, for the selection tools
- Python 3.10+; `mcp` and `pymupdf` are pulled in as dependencies

## Install

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/david-wang-0/okular-mcp
```

Then register it with your MCP client. Claude Code, user scope:

```sh
claude mcp add okular -s user -- okular-mcp
```

Codex CLI:

```sh
codex mcp add okular -- okular-mcp
```

Any other client: run `okular-mcp` as a stdio server.

## Workflow

1. Read in Okular. Highlight with the highlighter tool, add notes to highlights.
2. **Save** (Ctrl+S). Okular keeps annotations in memory until then, and the
   server only sees what is in the file.
3. Ask the LLM to collect the highlights (`annotations`), discuss the page you
   are on (`viewer_state`, `page_text`), or explain the phrase under your mouse
   (`get_selection`).
4. Let it mark back (`mark_text`; `style="underline"` with `note_in_margin` is the
   least intrusive), answer a note of yours as a threaded reply (`add_note` with
   `reply_to`), or clean up its own marks (`remove_annotation`). The server saves incrementally and asks Okular to
   reload, so the change appears in the viewer.

## Gotchas

- **Save before any writing tool** (`mark_text`, `add_note`, `remove_annotation`). If Okular has unsaved annotations when the
  file changes on disk, it offers a reload and the unsaved marks are lost if
  you accept. The tool descriptions tell the model to ask you to save first.
- **Tabs.** With several documents in tabs of one window, Okular's D-Bus
  interface reports the first-opened tab, not the active one
  ([KDE bug 502482](https://bugs.kde.org/show_bug.cgi?id=502482)). Use
  separate windows if you rely on `viewer_state`.
- **Selection text is Okular's text layer**, returned verbatim: hyphenation
  and column order can be messy.
- **Minimal client environments.** Some MCP clients (Codex, the Python SDK's
  client) start servers with only `HOME`, `PATH`, `SHELL` and `USER`. The server
  then recovers `DBUS_SESSION_BUS_ADDRESS` and `WAYLAND_DISPLAY` from
  `XDG_RUNTIME_DIR` (or `/run/user/<uid>`), so no per-client `env` block is
  needed on a systemd desktop.
- Highlighted text is recovered from the annotation's quads, so it can pick
  up a neighbouring word on tight line spacing.

## Development

```sh
uv venv && uv pip install -e '.[test]'
.venv/bin/pytest
```

`okular_mcp/viewer.py` is the only Okular-specific module (`windows`,
`current`, `goto`, `reload`); `selection.py` and `pdf.py` are viewer-agnostic,
so another viewer backend can be added without changing the tool surface.

## License

MIT
