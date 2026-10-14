"""pyproject.toml's console scripts resolve to callables, and installing the package
creates them (the commands Claude Code, LaTeX Workshop and the README name)."""

import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from mcpclient import coverage_env

tomllib = pytest.importorskip("tomllib")

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = {"okular-mcp": "okular_mcp.server:main", "okular-mcp-hook": "okular_mcp.hook:main",
            "okular-mcp-open": "okular_mcp.open:main"}


def scripts():
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["scripts"]


def test_the_three_commands_are_declared():
    assert scripts() == COMMANDS


@pytest.mark.parametrize("name", COMMANDS)
def test_each_command_resolves_to_a_callable(name):
    module, attr = scripts()[name].split(":")
    assert callable(getattr(importlib.import_module(module), attr))


def installed():
    try:
        dist = importlib.metadata.distribution("okular-mcp")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("okular-mcp is not installed in this environment (uv pip install -e '.[test]')")
    return dist


def test_installed_metadata_matches_pyproject():
    eps = {e.name: e.value for e in installed().entry_points if e.group == "console_scripts"}
    assert eps == COMMANDS


@pytest.mark.parametrize("name", COMMANDS)
def test_each_command_exists_after_install(name):
    installed()
    exe = os.path.join(sysconfig.get_path("scripts"), name)
    assert os.path.isfile(exe) and os.access(exe, os.X_OK), exe


def test_installed_commands_run():
    """The hook exits 0 silently on empty input; okular-mcp-open --help exits 0."""
    installed()
    bin_dir = sysconfig.get_path("scripts")
    env = {"PATH": "/nonexistent", "XDG_RUNTIME_DIR": "/nonexistent"}
    hook = subprocess.run([os.path.join(bin_dir, "okular-mcp-hook")], input="", env=env,
                          capture_output=True, text=True, timeout=10)
    assert (hook.returncode, hook.stdout, hook.stderr) == (0, "", "")
    help_ = subprocess.run([os.path.join(bin_dir, "okular-mcp-open"), "--help"], env=env,
                           capture_output=True, text=True, timeout=10)
    assert help_.returncode == 0 and help_.stdout.startswith("usage: okular-mcp-open <pdf>")


@pytest.mark.parametrize("module, argv", [("okular_mcp.hook", []), ("okular_mcp.open", ["--help"])])
def test_python_m_runs_the_commands(module, argv):
    """``python -m okular_mcp.<module>`` works as well, without an install."""
    env = {"PATH": "/nonexistent", "XDG_RUNTIME_DIR": "/nonexistent", "PYTHONPATH": str(ROOT), **coverage_env()}
    r = subprocess.run([sys.executable, "-m", module, *argv], input="", env=env, capture_output=True,
                       text=True, timeout=10)
    assert r.returncode == 0 and r.stderr == ""


def test_python_m_runs_the_server():
    """``python -m okular_mcp.server`` answers the MCP initialize request on stdio."""
    env = {"PATH": "/nonexistent", "XDG_RUNTIME_DIR": "/nonexistent", "PYTHONPATH": str(ROOT), **coverage_env()}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}}
    r = subprocess.run([sys.executable, "-m", "okular_mcp.server"], input=json.dumps(init) + "\n", env=env,
                       capture_output=True, text=True, timeout=20)
    answer = json.loads(r.stdout.splitlines()[0])
    assert answer["id"] == 1 and answer["result"]["serverInfo"]["name"] == "okular"
