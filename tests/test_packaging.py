"""pyproject.toml's console scripts resolve to callables, and installing the package
creates them (the commands Claude Code, LaTeX Workshop and the README name)."""

import importlib
import importlib.metadata
import os
import subprocess
import sysconfig
from pathlib import Path

import pytest

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
