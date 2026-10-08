"""Shared fixtures for the week 3 test suite."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from week3.server.bridge import BlenderBridge
from week3.server.config import Settings

from .fake_blender import FakeBlenderBridge
from .fake_bpy import FakeBlender

ADDON_PATH = Path(__file__).resolve().parents[1] / "blender_addon" / "blender_mcp_addon.py"


def make_settings(host: str, port: int, **overrides) -> Settings:
    """Build settings pointed at a test server, with retries sped up."""
    settings = Settings.__new__(Settings)
    settings.host = host
    settings.port = port
    settings.connect_timeout = overrides.pop("connect_timeout", 1.0)
    settings.command_timeout = overrides.pop("command_timeout", 5.0)
    settings.max_retries = overrides.pop("max_retries", 2)
    settings.backoff_base = overrides.pop("backoff_base", 0.01)
    settings.backoff_cap = overrides.pop("backoff_cap", 0.05)
    settings.min_request_interval = overrides.pop("min_request_interval", 0.0)
    settings.server_name = "blender-mcp"
    settings.server_version = "1.0.0"
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


@pytest.fixture
def fake_blender():
    """A running fake Blender bridge on an ephemeral port."""
    bridge = FakeBlenderBridge()
    bridge.start()
    yield bridge
    bridge.stop()


@pytest.fixture
def bridge(fake_blender):
    """A BlenderBridge wired to the fake Blender."""
    return BlenderBridge(make_settings(fake_blender.host, fake_blender.port))


@pytest.fixture
def fake_bpy():
    """A fake ``bpy`` module for exercising the add-on headlessly."""
    return FakeBlender()


@pytest.fixture
def free_port():
    """A TCP port that is currently closed, for unavailable-bridge tests."""
    from .fake_blender import free_port as find_free_port

    return find_free_port()


@pytest.fixture
def fake_blender_bridge_free_port():
    """A TCP port the add-on's own socket server can bind to."""
    from .fake_blender import free_port as find_free_port

    return find_free_port()


@pytest.fixture
def addon(monkeypatch, fake_bpy):
    """Import the real add-on file with ``bpy`` faked out.

    The add-on runs every command on Blender's main thread. In tests there is no
    main thread to hop to, so the executor is swapped for the inline one, which
    is exactly the fallback the add-on itself provides.
    """
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy.module)

    spec = importlib.util.spec_from_file_location("blender_mcp_addon_under_test", ADDON_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.executor = module.InlineExecutor()
    yield module
