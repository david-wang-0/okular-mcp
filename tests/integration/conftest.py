"""The real-Okular integration suite: headless Okulars on a private session bus.

Run with ``pytest -m integration`` (plain ``pytest`` deselects it). It needs
``okular``, ``dbus-run-session``, ``qdbus`` (or qdbus6), ``pdflatex``, ``synctex``
and ``pdftotext``, and skips with the list of what is missing otherwise; the test
with pdf-reader-writer-mcp also needs ``pdf-reader-writer-mcp`` on PATH.

It never touches the user's Okular or screen. ``dbus-run-session`` starts a private
bus for the whole session; every Okular, and every process of the code under test
(the MCP server, the hook, okular-mcp-open), runs with that bus's address, with
``QT_QPA_PLATFORM=offscreen``, without DISPLAY or WAYLAND_DISPLAY, and with its own
temporary XDG config, data, cache, state and runtime directories, so neither the
user's Okular settings (e.g. opening files as tabs of a running Okular) nor
``env.ensure_session_env`` can reach the real session. The code under test gets a
dummy WAYLAND_DISPLAY and a fake ``wl-paste`` (tests/fakes.py) for the selection.
Okular's output goes to files under the session's temporary directory, never to
pipes. Afterwards every process on that bus (Okulars, and daemons the bus started
for them, such as ksecretd) is killed, and only those.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fakes import FakeBin, entry_point
from mcpclient import Server

QDBUS_NAMES = ("qdbus", "qdbus6", "qdbus-qt6", "qdbus-qt5")
REQUIRED = ("okular", "dbus-run-session", "pdflatex", "synctex", "pdftotext")
DROP = ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY",
        "XDG_SESSION_TYPE", "XDG_SESSION_ID", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "SESSION_MANAGER",
        "KDE_FULL_SESSION", "KDE_SESSION_UID", "KDE_SESSION_VERSION", "QT_QPA_PLATFORM",
        "QT_QPA_PLATFORMTHEME", "XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID")
NO_WAYLAND = "okular-mcp-test-no-display"
"""The dummy WAYLAND_DISPLAY of the code under test: no such socket exists."""

HOLDER = """import os, signal, sys
with open(sys.argv[1] + ".tmp", "w") as f:
    f.write(os.environ["DBUS_SESSION_BUS_ADDRESS"] + "\\n" + str(os.getpid()))
os.rename(sys.argv[1] + ".tmp", sys.argv[1])
signal.pause()
"""
"""Run by dbus-run-session: reports the private bus's address and its own pid, then
waits; when it ends, dbus-run-session stops the bus."""

MAIN_TEX = r"""\documentclass{article}
\begin{document}
Page one alpha words here.
\newpage
Page two bravo words here.
\newpage
\input{sec/part}
\newpage
Page four delta words here.
\newpage
Page five echo words here.
\end{document}
"""

PART_TEX = """Page three charlie words here.

The quick brown fox jumps over the lazy dog.
"""


def until(fn, timeout=10.0, what="the condition"):
    """Poll ``fn`` until it returns something truthy (bounded); its value."""
    end = time.monotonic() + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() > end:
            raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")
        time.sleep(0.05)


def bus_path(address: str) -> str:
    """``unix:path=/tmp/dbus-XXXX`` of an address like ``unix:path=/tmp/dbus-XXXX,guid=...``."""
    return address.split(",")[0]


def on_bus(path: str) -> list[int]:
    """Our processes whose DBUS_SESSION_BUS_ADDRESS is the bus ``path``."""
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = f.read().split(b"\0")
        except OSError:
            continue
        for kv in env:
            if kv.startswith(b"DBUS_SESSION_BUS_ADDRESS="):
                if bus_path(kv.split(b"=", 1)[1].decode(errors="replace")) == path:
                    found.append(int(pid))
                break
    return found


def comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


def kill(pids: list[int], timeout=5.0) -> None:
    """SIGTERM, then SIGKILL what is left after ``timeout``."""
    for sig, wait in ((signal.SIGTERM, timeout), (signal.SIGKILL, timeout)):
        live = []
        for pid in pids:
            try:
                os.kill(pid, sig)
                live.append(pid)
            except ProcessLookupError:
                pass
        end = time.monotonic() + wait
        while live and time.monotonic() < end:
            live = [p for p in live if os.path.exists(f"/proc/{p}") and _alive(p)]
            time.sleep(0.05)
        if not live:
            return
        pids = live


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


class Session:
    """The private bus, the environments, the documents, and helpers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.logs = root / "logs"
        self.logs.mkdir()
        xdg = {}
        for name in ("config", "data", "cache", "state", "runtime"):
            d = root / "xdg" / name
            d.mkdir(parents=True, mode=0o700)
            xdg[name] = str(d)
        self.data_home = Path(xdg["data"])
        base = {k: v for k, v in os.environ.items() if k not in DROP}
        base.update(QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=xdg["config"], XDG_DATA_HOME=xdg["data"],
                    XDG_CACHE_HOME=xdg["cache"], XDG_STATE_HOME=xdg["state"], XDG_RUNTIME_DIR=xdg["runtime"],
                    QT_LOGGING_RULES="org.kde.okular.*.debug=true", QT_FORCE_STDERR_LOGGING="1")
        self.base = base
        self.fake = FakeBin(root / "fake", tools=("wl-paste",))
        self.qdbus_exe = next(shutil.which(n) for n in QDBUS_NAMES if shutil.which(n))
        self.okulars: list[subprocess.Popen] = []
        self.log: dict[str, Path] = {}
        """The output file of each Okular started here, by service."""
        self.address = self.bus = ""
        self.holder = 0
        self.drs: subprocess.Popen | None = None

    # --- the private bus ---

    def start_bus(self) -> None:
        addr = self.root / "bus-address"
        log = open(self.logs / "dbus-run-session.log", "w")
        self.drs = subprocess.Popen(["dbus-run-session", "--", sys.executable, "-S", "-c", HOLDER, str(addr)],
                                    env=self.base, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    start_new_session=True)
        log.close()
        until(lambda: addr.exists() or self.drs.poll() is not None, 15, "dbus-run-session")
        if not addr.exists():
            raise RuntimeError(f"dbus-run-session exited ({self.drs.returncode}); see {self.logs}")
        address, holder = addr.read_text().split("\n")
        self.address, self.holder, self.bus = address, int(holder), bus_path(address)
        assert self.bus != f"unix:path=/run/user/{os.getuid()}/bus", "not a private bus"
        assert self.bus.startswith("unix:path=/"), self.address
        self.okular_env = {**self.base, "DBUS_SESSION_BUS_ADDRESS": self.address}
        self.env = {**self.okular_env, "WAYLAND_DISPLAY": NO_WAYLAND,
                    "PATH": self.fake.path + os.pathsep + self.base.get("PATH", "")}

    def stop(self) -> None:
        """Kill everything on our bus, then the bus."""
        for p in self.okulars:
            if p.poll() is None:
                p.kill()
            p.wait()
        if self.bus:
            kill(on_bus(self.bus))
        if self.holder:
            try:
                os.kill(self.holder, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.drs is not None:
            try:
                self.drs.wait(10)
            except subprocess.TimeoutExpired:
                self.drs.kill()
                self.drs.wait()
        if self.bus:
            kill(on_bus(self.bus))
            left = on_bus(self.bus)
            assert not left, f"processes left on the private bus: {[(p, comm(p)) for p in left]}"

    # --- Okular ---

    def qdbus(self, *args: str, timeout: float = 5.0) -> str:
        out = subprocess.run([self.qdbus_exe, *args], env=self.okular_env, capture_output=True, text=True,
                             timeout=timeout)
        if out.returncode != 0:
            raise RuntimeError(f"qdbus {' '.join(args)}: {out.stderr.strip()}")
        return out.stdout.strip()

    def services(self) -> list[str]:
        """The Okular windows' ``org.kde.okular-<pid>`` names, in bus order."""
        return [n.strip() for n in self.qdbus().splitlines() if n.strip().startswith("org.kde.okular-")]

    def state(self, service: str) -> tuple[str, int, int]:
        """(currentDocument, currentPage, pages) of a window."""
        get = lambda m: self.qdbus(service, "/okular", f"org.kde.okular.{m}")  # noqa: E731
        return get("currentDocument"), int(get("currentPage") or 0), int(get("pages") or 0)

    def start(self, pdf: str | None = None) -> str:
        """A new headless Okular window (showing ``pdf``, on page 1); its service."""
        path = self.logs / f"okular-{len(self.okulars)}.log"
        with open(path, "w") as log:
            p = subprocess.Popen(["okular"], env=self.okular_env, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=log, start_new_session=True)
        self.okulars.append(p)
        service = f"org.kde.okular-{p.pid}"
        self.log[service] = path
        until(lambda: service in self.services(), 15, f"{service} on the bus")
        until(lambda: self._answers(service), 15, f"{service} answering")
        if pdf:
            self.load(service, pdf)
        return service

    def _answers(self, service: str) -> bool:
        try:
            self.qdbus(service, "/okular", "org.kde.okular.currentDocument")
            return True
        except RuntimeError:
            return False

    def load(self, service: str, pdf: str) -> None:
        from okular_mcp.viewer import file_url
        self.qdbus(service, "/okular", "org.kde.okular.openDocument", file_url(pdf))
        until(lambda: (lambda s: s[0] == pdf and s[2] >= 1)(self.state(service)), 10, f"{pdf} loaded")

    def go(self, service: str, page: int) -> None:
        self.qdbus(service, "/okular", "org.kde.okular.goToPage", str(page))
        until(lambda: self.state(service)[1] == page, 5, f"page {page}")

    def fresh(self) -> None:
        """No Okular on the bus, no remembered pages, no selection."""
        kill([p for p in on_bus(self.bus) if comm(p) == "okular"])
        for p in self.okulars:
            if p.poll() is None:
                p.kill()
            p.wait()
        until(lambda: not self.services(), 10, "no Okular on the bus")
        shutil.rmtree(self.data_home / "okular", ignore_errors=True)
        self.fake.reset()

    # --- the code under test ---

    def run(self, name: str, *argv: str, stdin: str = "", cwd: str | None = None, timeout: float = 60):
        """Run a console script with the code's environment: (exit code, stdout, stderr, seconds)."""
        t = time.monotonic()
        r = subprocess.run([*entry_point(name), *argv], input=stdin, env=self.env, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr, time.monotonic() - t


def build_docs(root: Path) -> dict[str, str]:
    """The test documents: a LaTeX project with SyncTeX (an \\input across pages) and copies."""
    proj = root / "proj"
    (proj / "sec").mkdir(parents=True)
    (proj / "main.tex").write_text(MAIN_TEX)
    (proj / "sec" / "part.tex").write_text(PART_TEX)
    for _ in range(2):
        r = subprocess.run(["pdflatex", "-synctex=1", "-interaction=nonstopmode", "main.tex"], cwd=proj,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"pdflatex failed:\n{r.stdout[-2000:]}")
    docs = root / "docs"
    (docs / "with space").mkdir(parents=True)
    (docs / "nosync").mkdir()
    out = {"main": str(proj / "main.pdf"), "proj": str(proj), "part": str(proj / "sec" / "part.tex"),
           "paper": str(docs / "paper.pdf"), "other": str(docs / "other.pdf"),
           "odd": str(docs / "with space" / "a b#c.pdf"), "nosync": str(docs / "nosync" / "main.pdf"),
           "broken": str(docs / "broken.pdf"), "link": str(docs / "link.pdf"), "docs": str(docs)}
    for name in ("paper", "other", "odd", "nosync"):
        shutil.copyfile(out["main"], out[name])
    Path(out["broken"]).write_bytes(b"%PDF-1.4\nthis is not a PDF\n")
    os.symlink(out["paper"], out["link"])
    return out


@pytest.fixture(scope="session")
def session(tmp_path_factory):
    missing = [t for t in REQUIRED if not shutil.which(t)]
    if not any(shutil.which(n) for n in QDBUS_NAMES):
        missing.append("qdbus")
    if missing:
        pytest.skip(f"the integration suite needs {', '.join(missing)} on PATH")
    s = Session(tmp_path_factory.mktemp("okular-integration"))
    s.docs = build_docs(s.root)
    try:
        s.start_bus()
        yield s
    finally:
        s.stop()


@pytest.fixture(scope="session")
def mcp(session):
    """The real okular-mcp server over stdio, on the private bus."""
    import mcpclient
    if mcpclient.Client is None:
        pytest.skip("the stdio client needs mcp >= 2 (mcp.Client)")
    cmd = entry_point("okular-mcp")
    with open(session.logs / "okular-mcp.log", "w") as log:
        server = Server(cmd[0], cmd[1:], env=session.env, errlog=log)
        try:
            yield server
        finally:
            server.close()


@pytest.fixture(autouse=True)
def fresh(session):
    """Each test starts with no Okular on the private bus, and ends without one."""
    assert os.environ.get("DBUS_SESSION_BUS_ADDRESS", "") != session.address  # pytest itself stays off it
    session.fresh()
    yield session
    session.fresh()
