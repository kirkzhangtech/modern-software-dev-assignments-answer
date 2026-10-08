"""End-to-end tests over a real STDIO transport.

The server is launched as a subprocess and driven by a real MCP ``ClientSession``
over stdio — the same way Claude Desktop, Claude Code, and Cursor launch it. This
is the only test that proves the transport wiring works, and it doubles as a guard
against the classic STDIO bug: any stray write to stdout would corrupt the
protocol stream and break these tests immediately.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .fake_blender import FakeBlenderBridge

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TOOLS = {
    "get_scene_info",
    "get_object_info",
    "create_object",
    "transform_object",
    "delete_object",
    "set_material",
    "render_scene",
    "get_viewport_screenshot",
}


def server_params(fake: FakeBlenderBridge) -> StdioServerParameters:
    """Launch the server as a subprocess, pointed at the fake Blender."""
    environment = dict(os.environ)
    environment.update(
        {
            "BLENDER_MCP_HOST": fake.host,
            "BLENDER_MCP_PORT": str(fake.port),
            "BLENDER_MCP_MAX_RETRIES": "1",
            "BLENDER_MCP_BACKOFF_BASE": "0.01",
            "BLENDER_MCP_MIN_INTERVAL": "0",
            "BLENDER_MCP_TIMEOUT": "10",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "week3.server"],
        env=environment,
        cwd=str(REPO_ROOT),
    )


@pytest.fixture
def fake_blender():
    bridge = FakeBlenderBridge()
    bridge.start()
    yield bridge
    bridge.stop()


async def with_params(params: StdioServerParameters, callback):
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            return await callback(session, initialized)


def run_with_params(params, callback):
    """Run a callback against a server launched with ``params``."""
    import anyio

    return anyio.run(with_params, params, callback)


def run(fake, callback):
    """Launch the server against the fake bridge and run the callback."""
    return run_with_params(server_params(fake), callback)


class TestProtocolHandshake:
    def test_client_can_initialize(self, fake_blender):
        async def check(session, initialized):
            return initialized

        result = run(fake_blender, check)
        assert result.server_info.name == "blender-mcp"
        assert result.protocol_version

    def test_server_sends_workflow_instructions(self, fake_blender):
        async def check(session, initialized):
            return initialized.instructions

        instructions = run(fake_blender, check)

        assert instructions, "the server should explain how to use it"
        assert "get_scene_info" in instructions
        assert "Start MCP Bridge" in instructions

    def test_every_tool_is_listed(self, fake_blender):
        async def check(session, initialized):
            return await session.list_tools()

        result = run(fake_blender, check)

        assert {tool.name for tool in result.tools} == EXPECTED_TOOLS

    def test_tools_carry_descriptions_and_schemas(self, fake_blender):
        async def check(session, initialized):
            return await session.list_tools()

        result = run(fake_blender, check)

        for tool in result.tools:
            assert tool.description
            assert tool.input_schema["type"] == "object"

    def test_stdout_carries_only_protocol_messages(self, fake_blender):
        """Reaching this point means nothing polluted the stdout protocol stream.

        The MCP framing is newline-delimited JSON. A single stray ``print`` in the
        server would make the handshake in ``with_session`` fail, so a successful
        initialize is the assertion. Startup logging is exercised here too, because
        the server emits an info line and a bridge warning before serving.
        """

        async def check(session, initialized):
            return await session.list_tools()

        result = run(fake_blender, check)
        assert len(result.tools) == len(EXPECTED_TOOLS)


class TestToolCallsEndToEnd:
    def test_create_then_inspect_the_scene(self, fake_blender):
        async def check(session, initialized):
            created = await session.call_tool(
                "create_object", {"object_type": "cube", "name": "Hero", "size": 3}
            )
            scene = await session.call_tool("get_scene_info", {})
            return created, scene

        created, scene = run(fake_blender, check)

        assert created.is_error is False
        assert json.loads(created.content[0].text)["name"] == "Hero"
        assert "Hero" in scene.content[0].text

    def test_object_appears_in_the_fake_scene(self, fake_blender):
        async def check(session, initialized):
            await session.call_tool("create_object", {"object_type": "sphere", "name": "Ball"})
            return await session.call_tool("get_object_info", {"name": "Ball"})

        result = run(fake_blender, check)

        assert result.is_error is False
        assert json.loads(result.content[0].text)["name"] == "Ball"

    def test_transform_then_delete(self, fake_blender):
        async def check(session, initialized):
            await session.call_tool("create_object", {"object_type": "cube", "name": "Temp"})
            moved = await session.call_tool(
                "transform_object", {"name": "Temp", "location": [1, 2, 3]}
            )
            deleted = await session.call_tool("delete_object", {"name": "Temp"})
            return moved, deleted

        moved, deleted = run(fake_blender, check)

        assert moved.is_error is False
        assert json.loads(deleted.content[0].text)["deleted"] == "Temp"
        assert fake_blender.scene.objects == {}

    def test_material_is_applied(self, fake_blender):
        async def check(session, initialized):
            await session.call_tool("create_object", {"object_type": "cube", "name": "Painted"})
            return await session.call_tool(
                "set_material",
                {"name": "Painted", "base_color": [1, 0, 0], "metallic": 0.7},
            )

        result = run(fake_blender, check)

        assert result.is_error is False
        assert fake_blender.scene.objects["Painted"]["materials"] == ["Painted_material"]

    def test_render_returns_a_path(self, fake_blender):
        async def check(session, initialized):
            return await session.call_tool("render_scene", {"resolution_x": 640})

        result = run(fake_blender, check)

        assert result.is_error is False
        assert json.loads(result.content[0].text)["resolution"][0] == 640

    def test_screenshot_returns_an_image_block(self, fake_blender):
        async def check(session, initialized):
            return await session.call_tool("get_viewport_screenshot", {})

        result = run(fake_blender, check)

        assert result.is_error is False
        assert result.content[0].type == "image"
        assert result.content[0].mime_type == "image/png"


class TestErrorsOverTheWire:
    def test_bad_arguments_surface_as_a_tool_error(self, fake_blender):
        async def check(session, initialized):
            return await session.call_tool("create_object", {"object_type": "teapot"})

        result = run(fake_blender, check)

        assert result.is_error is True
        assert "teapot" in result.content[0].text

    def test_missing_object_surfaces_as_a_tool_error(self, fake_blender):
        async def check(session, initialized):
            return await session.call_tool("get_object_info", {"name": "ghost"})

        result = run(fake_blender, check)

        assert result.is_error is True
        assert "ghost" in result.content[0].text

    def test_unknown_tool_name_is_a_protocol_error(self, fake_blender):
        async def check(session, initialized):
            return await session.call_tool("teleport_object", {})

        result = run(fake_blender, check)

        assert result.is_error is True

    def test_unreachable_blender_is_explained_not_crashed(self, fake_blender):
        """A dead bridge must produce guidance, not a dropped connection."""
        # Capture the parameters while the fake is still running, then kill it so
        # nothing is listening on the port when the server tries to connect.
        params = server_params(fake_blender)
        fake_blender.stop()

        async def check(session, initialized):
            return await session.call_tool("get_scene_info", {})

        result = run_with_params(params, check)

        assert result.is_error is True
        text = result.content[0].text
        assert "Blender" in text
        assert "Start MCP Bridge" in text

    def test_unavailable_bridge_does_not_kill_the_session(self, fake_blender):
        """A connection failure must leave the protocol session usable."""
        params = server_params(fake_blender)
        fake_blender.stop()

        async def check(session, initialized):
            failed = await session.call_tool("get_scene_info", {})
            listed = await session.list_tools()
            return failed, listed

        failed, listed = run_with_params(params, check)

        assert failed.is_error is True
        assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS
