import os
import subprocess

import pytest

from okular_mcp import env


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    return tmp_path


def test_c5_runtime_dir_prefers_xdg(rt):
    assert env.runtime_dir() == str(rt)


def test_c5_runtime_dir_falls_back_to_run_user_uid(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert env.runtime_dir() == f"/run/user/{os.getuid()}"


def test_c5_ensure_sets_xdg_runtime_dir_when_missing(monkeypatch):
    """XDG_RUNTIME_DIR itself is filled in with the /run/user/<uid> fallback."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "x")
    monkeypatch.setenv("WAYLAND_DISPLAY", "x")
    env.ensure_session_env()
    assert os.environ["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"


def test_c5_dbus_unset_when_no_bus_socket(rt):
    env.ensure_session_env()
    assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ


def test_c5_dbus_filled_from_bus_socket(rt):
    (rt / "bus").touch()
    env.ensure_session_env()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={rt}/bus"


def test_c5_dbus_existing_value_kept(rt, monkeypatch):
    (rt / "bus").touch()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/elsewhere")
    env.ensure_session_env()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/elsewhere"


def test_c5_wayland_unset_when_no_socket(rt):
    env.ensure_session_env()
    assert "WAYLAND_DISPLAY" not in os.environ


def test_c5_wayland_prefers_wayland_0(rt):
    (rt / "wayland-1").touch()
    (rt / "wayland-0").touch()
    env.ensure_session_env()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"


def test_c5_wayland_falls_back_to_wayland_1(rt):
    (rt / "wayland-1").touch()
    env.ensure_session_env()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"


def test_c5_wayland_existing_value_kept(rt, monkeypatch):
    (rt / "wayland-0").touch()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-7")
    env.ensure_session_env()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-7"


# --- the environment is filled in before any external tool runs ---

@pytest.fixture
def stripped(tmp_path, monkeypatch):
    """A minimal client environment: no bus or Wayland variables, sockets under XDG_RUNTIME_DIR.
    Returns the argv list of every subprocess.run, with the env each one would inherit."""
    (tmp_path / "bus").touch()
    (tmp_path / "wayland-0").touch()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    from okular_mcp import selection, viewer
    monkeypatch.setattr(viewer.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(selection.shutil, "which", lambda n: f"/usr/bin/{n}")
    runs = []

    def run(cmd, **kw):
        runs.append((cmd[0], os.environ.get("DBUS_SESSION_BUS_ADDRESS"), os.environ.get("WAYLAND_DISPLAY")))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", run)
    return runs


def test_c5_qdbus_runs_with_the_session_bus_filled_in(stripped, tmp_path):
    from okular_mcp import viewer
    with pytest.raises(viewer.ViewerError):
        viewer.entries()
    assert stripped == [("/usr/bin/qdbus", f"unix:path={tmp_path}/bus", "wayland-0")]


def test_c5_wl_paste_runs_with_wayland_filled_in(stripped, tmp_path):
    from okular_mcp import selection
    selection.read("clipboard")
    assert stripped == [("wl-paste", f"unix:path={tmp_path}/bus", "wayland-0")]


def test_c5_server_tools_fill_the_env_first(stripped, tmp_path):
    from okular_mcp import server
    server.get_selection()
    assert stripped and all(r[1:] == (f"unix:path={tmp_path}/bus", "wayland-0") for r in stripped)
    assert {r[0] for r in stripped} == {"wl-paste", "/usr/bin/qdbus"}


def test_c5_hook_fills_the_env_first(stripped, tmp_path, monkeypatch, capsys):
    import io
    from okular_mcp import hook
    monkeypatch.setattr("sys.stdin", io.StringIO('{"cwd": "/"}'))
    with pytest.raises(SystemExit):
        hook.main()
    assert stripped == [("/usr/bin/qdbus", f"unix:path={tmp_path}/bus", "wayland-0")]
