"""Entry point for the Blender MCP server.

Run it over STDIO, which is how Claude Desktop, Claude Code, and Cursor launch
MCP servers:

    poetry run python -m week3.server

Critical: a STDIO MCP server must never write to stdout. stdout *is* the
protocol channel, so a stray ``print`` corrupts the stream and the client drops
the connection. All logging therefore goes to stderr via ``logging.basicConfig``,
and no module in this package calls ``print``.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from .bridge import BlenderBridge
from .config import settings
from .tools import register_tools

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Controls a running Blender session through the Blender MCP Bridge add-on.

Workflow:
1. Call get_scene_info to learn what is already in the scene.
2. Create or modify objects with create_object, transform_object, and
   set_material.
3. Call get_viewport_screenshot to verify the result visually.
4. Call render_scene only when you need a final image, because it is slow.

If a tool reports that the bridge is unreachable, tell the user to open Blender
and start the bridge from View3D > Sidebar (N) > Blender MCP > Start MCP Bridge.
"""


def configure_logging() -> None:
    """Send all logs to stderr so stdout stays clean for the STDIO protocol."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def build_server(bridge: BlenderBridge | None = None) -> MCPServer:
    """Create the MCP server with every Blender tool registered."""
    server = MCPServer(
        name=settings.server_name,
        version=settings.server_version,
        instructions=INSTRUCTIONS,
    )
    register_tools(server, bridge or BlenderBridge())
    return server


def main() -> None:
    """Run the server over STDIO until the client disconnects."""
    configure_logging()

    logger.info(
        "Starting %s v%s, bridging to %s:%d",
        settings.server_name,
        settings.server_version,
        settings.host,
        settings.port,
    )

    if not BlenderBridge().is_available():
        # Not fatal: the user may start Blender after the client. The tools will
        # explain what to do if it is still missing when they are called.
        logger.warning(
            "Blender bridge not reachable at %s:%d. Tools will report this until "
            "Blender is running with the bridge started.",
            settings.host,
            settings.port,
        )

    build_server().run("stdio")


if __name__ == "__main__":
    main()
