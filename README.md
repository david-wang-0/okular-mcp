# okular-mcp

An [MCP](https://modelcontextprotocol.io) server that tells an LLM session **what
you are reading in [Okular](https://okular.kde.org/)**: which documents are open
and on what page, the text you have selected with the mouse (with its LaTeX
source lines when the PDF has SyncTeX data), and (when you ask) showing you a page.
A prompt hook for Claude Code adds the document, the page and your selection to
every prompt you type in a project that contains the PDF, and `okular-mcp-open`
lets LaTeX tools open a PDF or do a forward search in Okular.

This server does not read or write PDF content. It implements the viewer-state
contract in [`CONTRACT.md`](CONTRACT.md), and the PDF work is done by sibling
servers:

- **`pdf-reader-writer-mcp`**: page text with line numbers, links, outline,
  annotations, marks and notes written back, and `locate` (selection to line
  numbers). Every tool takes the `path` and `page` this server reports.
- **`vscode-pdf-mcp`**: the same contract for the PDF viewer inside VS Code.

`CONTRACT.md` is shared: both viewer servers keep an identical copy, and its
clauses (C1 to C8) are pinned by tests named after them (`test_c3_...`). Change it
in both repositories together, never in one.

## Tools

| Tool | What it does |
|---|---|
| `viewer_state()` | One entry per Okular window with a document open, the active one first (C1) |
| `get_selection(source="primary")` | The text selected with the mouse (`primary`), attributed to the active document and the page it is on, with its LaTeX source lines when the PDF has SyncTeX data; or the clipboard (`clipboard`), never attributed (C2) |
| `goto(page, path?)` | Show `page` of `path` (default: the active document), opening the file first if no window has it; returns its entry after the move. Meant for "show me where that is", not for the model to navigate on its own (C3) |

An **entry** is

```json
{"viewer": "okular", "window": "org.kde.okular-12345", "path": "/abs/paper.pdf",
 "page": 3, "pages": 12, "active": true}
```

`window` is the Okular process's D-Bus service name, `page` is 1-based, `pages`
the page count. Okular reports no focus, so the **active** entry is the first
window that has a document open. With nothing open (or Okular not running),
`viewer_state` and `goto` without a path fail with a message starting
`no Okular window with a PDF open`.

`get_selection` returns `{"text", "source"}`, plus `viewer`, `path` and `page`
for `source="primary"` when a document is open. `text` is `""` when nothing is
selected. The primary selection is system-wide: it is whatever was last selected
in any window, so it is attributed to Okular's active document on trust, and
Okular does not say where in the document it is. So the server looks for the
selected words in the text of the active document's current page, then the next
page, then the previous one (with poppler's `pdftotext`, the same text layer
Okular selects from), and `page` is the first page that has them, else the
current page. When they occur exactly once on that page and the PDF has a
`.synctex.gz` (or `.synctex`) next to it, `tex` gives their LaTeX source lines,
from `synctex edit` at the first and last word and one word of each printed line
between:

```json
{"text": "...", "source": "primary", "viewer": "okular", "path": "/abs/paper.pdf",
 "page": 4, "tex": [{"file": "/abs/sec/intro.tex", "lines": [12, 15]}]}
```

One entry per source file, in document order. A selection may start or end inside
a word, and a word TeX hyphenated at a line end matches with or without its
hyphen. pdfTeX tags the space after a source line's first word with the line
before, so `synctex edit` alone gives that word the previous line; the server
checks with `synctex view` whether the next line starts right after the word and
then takes that line, and it maps a paragraph's last word, which SyncTeX puts on
the blank line after it, to the line it is on.

`goto` fails with `no such file: <path>` for a path that does not exist, `cannot open <path>`
if Okular fails to load it, and `page N outside 1-M` before moving if the page is out of
range.

Errors reach the model as their message (as an MCP tool error), not as a
generic "Error executing tool".

## Prompt hook (Claude Code)

`okular-mcp-hook` is a `UserPromptSubmit` hook. Add it to a Claude Code
`settings.json` (user or project):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "okular-mcp-hook", "timeout": 5}]}
    ]
  }
}
```

When the active Okular document lies inside the session's working directory
(after resolving symlinks; a PDF in a sibling directory does not count), it adds

```
The user is reading /abs/project/paper.pdf in Okular, page 3 of 12.
Selected text (page 4):
the words you selected
Source: sec/intro.tex:12-15
```

to the prompt's context. Otherwise it adds nothing. The selection lines come only
when the selected words are found on the document's current page or a page next
to it (as for `get_selection`): the system primary selection cannot be tied to
Okular otherwise, and is often the text just selected in the terminal the prompt
is typed in. The text is cut at 2000 characters (` […]` marks the cut). The
`Source:` lines follow when the PDF has SyncTeX data (`:12` alone for a single
line), with the file relative to the working directory when it lies inside it.

The hook always exits 0, also on bad input or when Okular or the bus does not
answer (each qdbus call may take 0.3 s, and a 0.8 s watchdog ends it silently), so
it never blocks a prompt. The selection part has its own deadline, 0.6 s after the
hook starts: if reading the selection, `pdftotext` or `synctex` is slow or
missing, the hook leaves the selection out and still reports the page. Measured
on a 54-page paper with SyncTeX data, the whole hook takes about 0.26 s with a
selection found and mapped (about 0.14 s of it for the selection part) and about
0.13 s without one.

## LaTeX: `okular-mcp-open`

```sh
okular-mcp-open <pdf>                                         # open the PDF
okular-mcp-open --tex <file> --line <n> [--column <n>] <pdf>  # forward search
```

The same command line as `vscode-pdf-mcp-open`, so a LaTeX tool can be pointed
at either viewer. A window that already shows the PDF is left as it is;
otherwise a new Okular window opens it (`okular` started without arguments, which
never joins a running Okular as a tab). The forward search asks `synctex view`
for the page first, then hands Okular the URL `file://<pdf>#src:<line> <file>`
(the `--unique` forward search), in the window that shows the PDF or else a new
one. Okular has no column in a forward search, so `--column` is accepted and
ignored (a negative one means none, as LaTeX Workshop may pass). It prints
`<pdf>: page <n> of <pages>` (what Okular shows once it has settled), exits 1
with the reason on stderr when it cannot (`no such file: <pdf>`, `cannot open
<pdf>`, `no SyncTeX data for <file>:<line> in <pdf>`, Okular not starting) and
2 on bad arguments.

A forward search opens the document anew, like `okular --unique`, so Okular asks
to save unsaved annotations first; save in Okular before you jump.

LaTeX Workshop, in VS Code's `settings.json`:

```json
"latex-workshop.view.pdf.viewer": "external",
"latex-workshop.view.pdf.external.viewer.command": "okular-mcp-open",
"latex-workshop.view.pdf.external.viewer.args": ["%PDF%"],
"latex-workshop.view.pdf.external.synctex.command": "okular-mcp-open",
"latex-workshop.view.pdf.external.synctex.args": ["--tex", "%TEX%", "--line", "%LINE%", "%PDF%"]
```

Then *View LaTeX PDF* opens Okular and LaTeX Workshop's forward search
(Ctrl+Alt+J) lands in it. Use the absolute path of `okular-mcp-open` as the
command if VS Code does not have it on its `PATH`. Inverse search is Okular's
own: Shift+click in the page opens the source in the editor set under Settings →
Configure Okular → Editor.

## No reload tool, and saving

Nothing here writes the PDF, and there is no reload tool (C4). Okular's
"Reload document on file change" (WatchFile, on by default) re-renders the
document by itself after another tool saves it: an incremental save by
`pdf-reader-writer-mcp` showed up in about 1.5 s. But Okular keeps your own
**unsaved** annotations in memory and loses them when the file changes, so save
in Okular (Ctrl+S) before another tool writes to the file; the server's
instructions tell the model to ask you first.

## Requirements

Linux with KDE's Okular on the session D-Bus, plus:

- `qdbus` (Qt tools; installed with Okular on most distributions)
- `wl-clipboard` on Wayland, or `xclip`/`xsel` on X11, for `get_selection`
- optional: `pdftotext` (poppler; `poppler` or `poppler-utils`), to find the page
  of a selection; without it, `page` is the current page and the hook leaves
  the selection out
- optional: `synctex` (TeX Live), for the `tex` source lines, the hook's
  `Source:` lines and `okular-mcp-open --tex`
- Python 3.10+; `mcp` is the only dependency

## Install

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/david-wang-0/okular-mcp
```

This installs `okular-mcp` (the server), `okular-mcp-hook` and
`okular-mcp-open`. Register the server with your MCP client. Claude Code, user scope:

```sh
claude mcp add okular -s user -- okular-mcp
```

Codex CLI:

```sh
codex mcp add okular -- okular-mcp
```

Any other client: run `okular-mcp` as a stdio server.

## Gotchas

- **Tabs.** With several documents in tabs of one window, Okular's D-Bus
  interface reports the first-opened tab, not the active one
  ([KDE bug 502482](https://bugs.kde.org/show_bug.cgi?id=502482)). Use
  separate windows if you rely on `viewer_state`: the process registers a single
  `/okular` object, so other tabs are not reachable over D-Bus at all. With the
  vscode-pdf-external-open extension, run it without `--unique` (one window per
  PDF) and leave `--unique` to the LaTeX build viewer only; see that README.
- **Selection text is Okular's text layer**, returned verbatim: hyphenation
  and column order can be messy. A selection whose words the page's text does
  not have in that order (it spans a page break, or Okular split a formula
  differently from `pdftotext`) is not found: `page` is then the current page,
  without `tex`, and the hook leaves it out.
- **Minimal client environments.** Some MCP clients (Codex, the Python SDK's
  client) start servers with only `HOME`, `PATH`, `SHELL` and `USER`. Before any
  `qdbus`, `wl-paste`, `xclip` or `xsel` call, the server (and the hook) recover
  `DBUS_SESSION_BUS_ADDRESS` and `WAYLAND_DISPLAY` from `XDG_RUNTIME_DIR` (or
  `/run/user/<uid>`), so no per-client `env` block is needed on a systemd
  desktop (C5).

## Development

```sh
uv venv && uv pip install -e '.[test]'
.venv/bin/pytest
```

The tests run against fakes (a fake session bus behind `viewer._call`, a fake
`subprocess.run` and `PATH`, fake `pdftotext`/`synctex` output); they never start
Okular or touch the real bus. With `pdflatex`, `synctex` and `pdftotext`
installed, `tests/test_sourcelines.py` also compiles a small LaTeX document and
checks the selections' pages and source lines with the real tools.
`okular_mcp/viewer.py` is the only Okular-specific module (`windows`, `entries`,
`active`, `goto`, and `new_window`, `open_url`, `wait_loaded` for
`okular-mcp-open`), `selection.py` reads the system selection, `sourcelines.py`
finds it on the page and maps it to source lines, `hook.py` is the prompt hook,
`open.py` is `okular-mcp-open`.

## License

MIT
