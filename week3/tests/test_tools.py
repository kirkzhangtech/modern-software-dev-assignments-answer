"""Tests for the MCP tools.

Tools are registered on a real ``MCPServer`` and invoked through
``server.call_tool``, so each test exercises schema validation, the handler, the
bridge, and response conversion exactly as a client would.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from week3.server.bridge import BlenderBridge
from week3.server.tools import register_tools

from .conftest import make_settings
from .fake_blender import TINY_PNG, free_port

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


def build_server(bridge: BlenderBridge) -> MCPServer:
    server = MCPServer(name="blender-mcp-test", version="0.0.1")
    register_tools(server, bridge)
    return server


def call(server: MCPServer, name: str, arguments: dict):
    """Invoke a tool and return its result, failing the test if it errors."""
    return asyncio.run(server.call_tool(name, arguments))


@pytest.fixture
def server(bridge):
    return build_server(bridge)


class TestRegistration:
    def test_every_expected_tool_is_exposed(self, server):
        tools = asyncio.run(server.list_tools())
        assert {tool.name for tool in tools} == EXPECTED_TOOLS

    def test_every_tool_has_a_description(self, server):
        """Descriptions are how the model decides which tool to call."""
        tools = asyncio.run(server.list_tools())
        for tool in tools:
            assert tool.description, f"{tool.name} has no description"
            assert len(tool.description) > 40, f"{tool.name}'s description is too thin"

    def test_descriptions_cover_parameters(self, server):
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        schema = tools["create_object"].input_schema

        assert "object_type" in schema["properties"]
        assert "location" in schema["properties"]


class TestSceneInspection:
    def test_get_scene_info_returns_the_scene(self, server):
        result = call(server, "get_scene_info", {})

        assert result.is_error is False
        assert "Scene" in result.content[0].text

    def test_get_object_info_returns_details(self, server, fake_blender):
        fake_blender.scene.create("Cube", "cube", [0, 0, 0], 2.0)

        result = call(server, "get_object_info", {"name": "Cube"})

        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["name"] == "Cube"
        assert payload["vertex_count"] == 8

    def test_get_object_info_for_missing_object_is_an_error(self, server):
        with pytest.raises(ToolError) as excinfo:
            call(server, "get_object_info", {"name": "does-not-exist"})

        message = str(excinfo.value)
        assert "does-not-exist" in message
        assert "Hint" in message

    def test_blank_object_name_is_rejected_before_the_bridge(self, server):
        with pytest.raises(ToolError, match="non-empty"):
            call(server, "get_object_info", {"name": "   "})


class TestCreateObject:
    def test_create_cube(self, server):
        result = call(server, "create_object", {"object_type": "cube", "name": "MyCube"})

        assert result.is_error is False
        assert "MyCube" in result.content[0].text

    @pytest.mark.parametrize(
        "object_type",
        ["cube", "sphere", "cylinder", "plane", "cone", "torus", "camera", "light"],
    )
    def test_every_documented_type_is_creatable(self, server, object_type):
        result = call(server, "create_object", {"object_type": object_type})
        assert result.is_error is False

    def test_unknown_type_is_rejected(self, server):
        with pytest.raises(ToolError) as excinfo:
            call(server, "create_object", {"object_type": "teapot"})

        assert "teapot" in str(excinfo.value)

    def test_non_positive_size_is_rejected(self, server):
        with pytest.raises(ToolError, match="greater than 0"):
            call(server, "create_object", {"object_type": "cube", "size": 0})

    def test_wrong_location_length_is_rejected(self, server):
        with pytest.raises(ToolError, match="exactly 3 numbers"):
            call(server, "create_object", {"object_type": "cube", "location": [1, 2]})

    def test_name_is_optional(self, server):
        result = call(server, "create_object", {"object_type": "cube"})
        assert result.is_error is False

    def test_light_type_reaches_the_bridge(self, server, fake_blender):
        call(server, "create_object", {"object_type": "light", "light_type": "SUN"})

        request = next(r for r in fake_blender.requests if r["type"] == "create_object")
        assert request["params"]["light_type"] == "SUN"


class TestTransform:
    def test_move_an_object(self, server, fake_blender):
        fake_blender.scene.create("Cube", "cube", [0, 0, 0], 2.0)

        result = call(server, "transform_object", {"name": "Cube", "location": [1, 2, 3]})

        assert result.is_error is False
        assert fake_blender.scene.objects["Cube"]["location"] == [1.0, 2.0, 3.0]

    def test_omitted_axes_are_left_alone(self, server, fake_blender):
        fake_blender.scene.create("Cube", "cube", [5, 5, 5], 2.0)

        call(server, "transform_object", {"name": "Cube", "location": [1, 1, 1]})

        request = next(r for r in fake_blender.requests if r["type"] == "transform_object")
        assert "rotation" not in request["params"]
        assert "scale" not in request["params"]

    def test_rotation_is_passed_through_as_degrees(self, server, fake_blender):
        fake_blender.scene.create("Cube", "cube", [0, 0, 0], 2.0)

        call(server, "transform_object", {"name": "Cube", "rotation": [0, 0, 90]})

        request = next(r for r in fake_blender.requests if r["type"] == "transform_object")
        assert request["params"]["rotation"] == [0.0, 0.0, 90.0]

    def test_no_arguments_is_rejected(self, server):
        with pytest.raises(ToolError, match="at least one of location, rotation, or scale"):
            call(server, "transform_object", {"name": "Cube"})

    def test_zero_scale_is_rejected(self, server):
        with pytest.raises(ToolError, match="positive"):
            call(server, "transform_object", {"name": "Cube", "scale": [1, 0, 1]})

    def test_missing_object_is_reported(self, server):
        with pytest.raises(ToolError, match="not_found|No object"):
            call(server, "transform_object", {"name": "ghost", "location": [0, 0, 0]})


class TestDelete:
    def test_delete_existing_object(self, server, fake_blender):
        fake_blender.scene.create("Doomed", "cube", [0, 0, 0], 2.0)

        result = call(server, "delete_object", {"name": "Doomed"})

        assert result.is_error is False
        assert "Doomed" not in fake_blender.scene.objects

    def test_delete_missing_object_is_an_error(self, server):
        with pytest.raises(ToolError):
            call(server, "delete_object", {"name": "ghost"})


class TestMaterial:
    def _mesh(self, fake_blender, name="Cube"):
        fake_blender.scene.create(name, "cube", [0, 0, 0], 2.0)

    def test_apply_material(self, server, fake_blender):
        self._mesh(fake_blender)

        result = call(
            server,
            "set_material",
            {"name": "Cube", "base_color": [1.0, 0.0, 0.0], "metallic": 0.8, "roughness": 0.2},
        )

        assert result.is_error is False
        assert fake_blender.scene.objects["Cube"]["materials"] == ["Cube_material"]

    def test_colour_is_forwarded(self, server, fake_blender):
        self._mesh(fake_blender)

        call(server, "set_material", {"name": "Cube", "base_color": [0.1, 0.2, 0.3]})

        request = next(r for r in fake_blender.requests if r["type"] == "set_material")
        assert request["params"]["base_color"] == [0.1, 0.2, 0.3]

    @pytest.mark.parametrize(
        "color",
        [[2.0, 0.0, 0.0], [-0.1, 0.0, 0.0], [0.5, 0.5], [0.5, 0.5, 0.5, 0.5]],
    )
    def test_invalid_colours_are_rejected(self, server, color):
        with pytest.raises(ToolError):
            call(server, "set_material", {"name": "Cube", "base_color": color})

    @pytest.mark.parametrize("value", [-0.1, 1.1])
    def test_out_of_range_metallic_is_rejected(self, server, value):
        with pytest.raises(ToolError, match="metallic"):
            call(server, "set_material", {"name": "Cube", "metallic": value})

    def test_custom_material_name_is_honoured(self, server, fake_blender):
        self._mesh(fake_blender)

        call(server, "set_material", {"name": "Cube", "material_name": "RedPaint"})

        assert fake_blender.scene.objects["Cube"]["materials"] == ["RedPaint"]


class TestRender:
    def test_render_returns_a_file_path(self, server):
        result = call(server, "render_scene", {})

        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["filepath"].endswith(".png")
        assert payload["size_bytes"] > 0

    def test_resolution_is_validated(self, server):
        with pytest.raises(ToolError, match="positive"):
            call(server, "render_scene", {"resolution_x": -1})

    def test_render_failure_is_reported(self, fake_blender):
        fake_blender.fail_commands["render_scene"] = "no_camera"
        bridge = BlenderBridge(make_settings(fake_blender.host, fake_blender.port))
        server = build_server(bridge)

        with pytest.raises(ToolError) as excinfo:
            call(server, "render_scene", {})

        assert "no_camera" in str(excinfo.value)


class TestViewportScreenshot:
    def test_screenshot_returns_png_content(self, server):
        result = call(server, "get_viewport_screenshot", {})

        assert result.is_error is False
        block = result.content[0]
        assert block.type == "image"
        assert block.mime_type == "image/png"

    def test_screenshot_bytes_round_trip(self, server):
        result = call(server, "get_viewport_screenshot", {})

        assert base64.b64decode(result.content[0].data) == TINY_PNG

    def test_screenshot_failure_is_reported(self, fake_blender):
        fake_blender.fail_commands["get_viewport_screenshot"] = "viewport_unavailable"
        bridge = BlenderBridge(make_settings(fake_blender.host, fake_blender.port))
        server = build_server(bridge)

        with pytest.raises(ToolError, match="viewport_unavailable"):
            call(server, "get_viewport_screenshot", {})


class TestUnavailableBridge:
    def test_tools_explain_how_to_start_blender(self):
        bridge = BlenderBridge(
            make_settings("127.0.0.1", free_port(), max_retries=1, backoff_base=0.001)
        )
        server = build_server(bridge)

        with pytest.raises(ToolError) as excinfo:
            call(server, "get_scene_info", {})

        message = str(excinfo.value)
        assert "Blender is running" in message
        assert "Start MCP Bridge" in message

    def test_unexpected_errors_are_not_leaked_raw(self, monkeypatch):
        class Exploding:
            def request(self, command, params=None):
                raise KeyError("boom")

        server = build_server(Exploding())  # type: ignore[arg-type]

        with pytest.raises(ToolError) as excinfo:
            call(server, "get_scene_info", {})

        assert "Unexpected error" in str(excinfo.value)
