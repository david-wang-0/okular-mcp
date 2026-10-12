# okular-mcp

An [MCP](https://modelcontextprotocol.io) server that tells an LLM session **what
you are reading in [Okular](https://okular.kde.org/)**: which documents are open
and on what page, the text you have selected with the mouse, and (when you ask)
showing you a page. A prompt hook for Claude Code adds the document and page to
every prompt you type in a project that contains the PDF.

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
| `get_selection(source="primary")` | The text selected with the mouse (`primary`), attributed to the active document, or the clipboard (`clipboard`), never attributed (C2) |
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

`get_selection` returns `{"text", "source"}`, plus `viewer`, `path` and `page` of
the active entry for `source="primary"` when a document is open. `text` is `""`
when nothing is selected. The primary selection is system-wide: it is whatever
was last selected in any window, so it is attributed to Okular's active
document on trust.

`goto` fails with `no such file: <path>` for a path that does not exist and with
`page N outside 1-M` before moving if the page is out of range.

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
```

to the prompt's context. Otherwise it adds nothing. It never adds the selection:
the system primary selection cannot be tied to Okular, and is often the text just
selected in the terminal the prompt is typed in; the model can still call
`get_selection`. The hook always exits 0, also on bad input or when Okular or the
bus does not answer (each qdbus call may take 0.3 s, and a 0.8 s watchdog ends it
silently), so it never blocks a prompt.

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
- Python 3.10+; `mcp` is the only dependency

## Install

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/david-wang-0/okular-mcp
```

This installs both `okular-mcp` (the server) and `okular-mcp-hook`. Register the
server with your MCP client. Claude Code, user scope:

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
  and column order can be messy.
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
`subprocess.run` and `PATH`); they never start Okular or touch the real bus.
`okular_mcp/viewer.py` is the only Okular-specific module (`windows`, `entries`,
`active`, `goto`), `selection.py` reads the system selection, `hook.py` is the
prompt hook.

## License

MIT
