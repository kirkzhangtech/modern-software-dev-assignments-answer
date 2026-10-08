"""Blender MCP server: exposes a running Blender session as MCP tools."""

from .bridge import BlenderBridge
from .main import build_server, main

__all__ = ["BlenderBridge", "build_server", "main"]
