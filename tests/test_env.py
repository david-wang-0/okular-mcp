import os

import pytest

from okular_mcp import env


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    return tmp_path


def test_runtime_dir_prefers_xdg(rt):
    assert env.runtime_dir() == str(rt)


def test_runtime_dir_falls_back_to_run_user_uid(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert env.runtime_dir() == f"/run/user/{os.getuid()}"


def test_ensure_sets_xdg_runtime_dir_when_missing(monkeypatch):
    """XDG_RUNTIME_DIR itself is filled in with the /run/user/<uid> fallback."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "x")
    monkeypatch.setenv("WAYLAND_DISPLAY", "x")
    env.ensure_session_env()
    assert os.environ["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"


def test_dbus_unset_when_no_bus_socket(rt):
    env.ensure_session_env()
    assert "DBUS_SESSION_BUS_ADDRESS" not in os.environ


def test_dbus_filled_from_bus_socket(rt):
    (rt / "bus").touch()
    env.ensure_session_env()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={rt}/bus"


def test_dbus_existing_value_kept(rt, monkeypatch):
    (rt / "bus").touch()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/elsewhere")
    env.ensure_session_env()
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/elsewhere"


def test_wayland_unset_when_no_socket(rt):
    env.ensure_session_env()
    assert "WAYLAND_DISPLAY" not in os.environ


def test_wayland_prefers_wayland_0(rt):
    (rt / "wayland-1").touch()
    (rt / "wayland-0").touch()
    env.ensure_session_env()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"


def test_wayland_falls_back_to_wayland_1(rt):
    (rt / "wayland-1").touch()
    env.ensure_session_env()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-1"


def test_wayland_existing_value_kept(rt, monkeypatch):
    (rt / "wayland-0").touch()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-7")
    env.ensure_session_env()
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-7"
