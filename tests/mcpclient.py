"""A synchronous MCP client for tests: a real server process over stdio, driven by the
``mcp`` package's client on an event loop in a background thread.

``Server(command, args, env)`` starts the server and connects (the initialize
handshake, as Claude Code does); ``call`` and ``tools`` block until the answer is
there; ``close`` ends the session and the process.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import threading
from typing import Any

from mcp.client.stdio import StdioServerParameters, stdio_client

try:  # mcp >= 2
    from mcp import Client
except ImportError:  # pragma: no cover - mcp 1.x has no Client
    Client = None

TIMEOUT = 30.0


def coverage_env() -> dict[str, str]:
    """The variables coverage.py puts in the environment to measure subprocesses too
    (``patch = ["subprocess"]``); an MCP client passes on only a few of the parent's."""
    return {k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")}


class Server:
    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None,
                 cwd: str | None = None, errlog=None) -> None:
        if Client is None:  # pragma: no cover
            raise RuntimeError("the stdio tests need mcp >= 2 (mcp.Client)")
        self.params = StdioServerParameters(command=command, args=args or [], env=env, cwd=cwd)
        self.errlog = errlog
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        self.main = asyncio.run_coroutine_threadsafe(self._main(ready), self.loop)
        try:
            ready.result(TIMEOUT)
        except BaseException:
            self.close()
            raise

    async def _main(self, ready: concurrent.futures.Future) -> None:
        # The client's task group must be entered and left in one task, so it lives here.
        try:
            # The server's stderr goes to ``errlog`` (a file) when given, else to ours.
            server = stdio_client(self.params, errlog=self.errlog) if self.errlog else self.params
            async with Client(server, mode="legacy") as client:
                self.client, self.stop = client, asyncio.Event()
                ready.set_result(None)
                await self.stop.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            raise

    def _run(self, coro) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(TIMEOUT)

    def call(self, name: str, args: dict[str, Any] | None = None):
        """The ``CallToolResult``."""
        return self._run(self.client.call_tool(name, args or {}))

    def tools(self) -> dict[str, Any]:
        return {t.name: t for t in self._run(self.client.list_tools()).tools}

    @property
    def instructions(self) -> str | None:
        return self.client.instructions

    @property
    def name(self) -> str | None:
        info = self.client.server_info
        return info.name if info else None

    def close(self) -> None:
        if getattr(self, "stop", None) is not None:
            self.loop.call_soon_threadsafe(self.stop.set)
        try:
            self.main.result(TIMEOUT)
        except BaseException:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(TIMEOUT)
        self.loop.close()


def text(result) -> str:
    """The text the model sees."""
    return "".join(getattr(c, "text", "") for c in result.content)


def data(result) -> Any:
    """A successful result's JSON: one object per content block (a list is sent as one
    block per item), a single block's object as it is."""
    assert not result.is_error, text(result)
    items = [json.loads(c.text) for c in result.content]
    return items[0] if len(items) == 1 else items
