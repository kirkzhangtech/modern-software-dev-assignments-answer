"""Configuration for the Blender MCP server.

Every value can be overridden with an environment variable, so the same code
runs against a local Blender on the default port or a different one on a shared
workstation.
"""

from __future__ import annotations

import os


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Settings:
    """Runtime settings for the Blender bridge and the MCP server."""

    def __init__(self) -> None:
        # --- Bridge connection -------------------------------------------
        self.host: str = _env_str("BLENDER_MCP_HOST", "127.0.0.1")
        self.port: int = _env_int("BLENDER_MCP_PORT", 9876)

        # How long to wait for the bridge to accept a connection, and how long
        # to wait for a command to come back. Rendering is slow, so the command
        # timeout is generous while the connect timeout stays tight.
        self.connect_timeout: float = _env_float("BLENDER_MCP_CONNECT_TIMEOUT", 5.0)
        self.command_timeout: float = _env_float("BLENDER_MCP_TIMEOUT", 120.0)

        # --- Resilience ---------------------------------------------------
        # Blender may still be starting up when the server is first used, so
        # connection failures are retried with exponential backoff.
        self.max_retries: int = _env_int("BLENDER_MCP_MAX_RETRIES", 3)
        self.backoff_base: float = _env_float("BLENDER_MCP_BACKOFF_BASE", 0.5)
        self.backoff_cap: float = _env_float("BLENDER_MCP_BACKOFF_CAP", 8.0)

        # Blender runs commands on its main thread, so flooding it with requests
        # starves the UI. Enforce a minimum gap between outgoing commands.
        self.min_request_interval: float = _env_float("BLENDER_MCP_MIN_INTERVAL", 0.05)

        # --- Server metadata ---------------------------------------------
        self.server_name: str = _env_str("BLENDER_MCP_SERVER_NAME", "blender-mcp")
        self.server_version: str = _env_str("BLENDER_MCP_SERVER_VERSION", "1.0.0")


settings = Settings()