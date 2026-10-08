"""Domain exceptions for the Blender MCP server.

The bridge and the tools raise these instead of leaking ``socket`` or ``json``
errors. Each one carries a message written for a *language model* to read: it
states what went wrong and what to do about it.
"""

from __future__ import annotations


class BlenderMCPError(Exception):
    """Base class for every error this server raises."""


class BlenderUnavailableError(BlenderMCPError):
    """The bridge is not reachable.

    Usually means Blender is closed, the add-on is disabled, or the bridge has
    not been started from the sidebar.
    """

    def __init__(self, host: str, port: int, detail: str) -> None:
        self.host = host
        self.port = port
        super().__init__(
            f"Could not connect to the Blender bridge at {host}:{port} after "
            f"several attempts ({detail}). "
            "Check that: (1) Blender is running, (2) the 'Blender MCP Bridge' "
            "add-on is enabled, and (3) the bridge is started from "
            "View3D > Sidebar (N) > Blender MCP > Start MCP Bridge."
        )


class BlenderTimeoutError(BlenderMCPError):
    """The bridge accepted the connection but did not answer in time."""

    def __init__(self, command: str, timeout: float) -> None:
        self.command = command
        self.timeout = timeout
        super().__init__(
            f"Blender did not answer '{command}' within {timeout:g}s. "
            "A long render or an open modal dialog in Blender will block it. "
            "Dismiss any dialog, or raise BLENDER_MCP_TIMEOUT."
        )


class BlenderProtocolError(BlenderMCPError):
    """The bridge sent something that is not a valid response."""


class BlenderCommandError(BlenderMCPError):
    """Blender reached the command but refused to run it."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        self.code = code
        self.hint = hint
        text = f"Blender rejected the command ({code}): {message}"
        if hint:
            text += f" Hint: {hint}"
        super().__init__(text)


class InvalidInputError(BlenderMCPError):
    """A tool received arguments that are structurally valid but nonsensical."""