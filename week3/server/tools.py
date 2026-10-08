"""MCP tool definitions for the Blender bridge.

Tool design notes
-----------------
* Names are ``verb_noun`` and say what they do, because a model picks tools by
  name alone.
* Docstrings are the tool descriptions the model reads. Each one states the
  purpose, what comes back, and any limit.
* Type hints become the JSON schema, so the SDK rejects malformed arguments
  before the handler runs. Business rules (colour ranges, non-empty names) are
  validated in the handler and reported as :class:`ToolError`.
* Every handler returns data, never leaks a raw exception, and its failure text
  explains what to do next.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from mcp import types
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .bridge import BlenderBridge
from .errors import BlenderMCPError, InvalidInputError

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 4 * 1024 * 1024


def _fail(exc: Exception) -> ToolError:
    """Translate a domain exception into a message a model can act on."""
    if isinstance(exc, BlenderMCPError):
        return ToolError(str(exc))
    logger.exception("Unexpected tool failure")
    return ToolError(f"Unexpected error: {type(exc).__name__}: {exc}")


def _validate_color(color: list[float] | None) -> list[float] | None:
    """Check an RGB triple, raising :class:`InvalidInputError` when out of range."""
    if color is None:
        return None
    if len(color) != 3:
        raise InvalidInputError(
            f"base_color must have exactly 3 components (r, g, b), got {len(color)}."
        )
    for component in color:
        if not 0.0 <= component <= 1.0:
            raise InvalidInputError(
                "base_color components are 0.0-1.0 floats, where (1, 0, 0) is red. "
                f"Got {color}."
            )
    return [float(component) for component in color]


def register_tools(server: MCPServer, bridge: BlenderBridge) -> None:
    """Attach every Blender tool to ``server``."""

    @server.tool()
    def get_scene_info() -> dict[str, Any]:
        """Inspect the currently open Blender scene.

        Returns the scene name, the blend file path, the current frame, and a
        list of every object with its name, type, and world-space location.
        Use this first to learn what exists before creating or editing anything.
        """
        try:
            return bridge.request("get_scene_info")
        except Exception as exc:  # noqa: BLE001 - reported as a tool error
            raise _fail(exc) from exc

    @server.tool()
    def get_object_info(name: str) -> dict[str, Any]:
        """Get detailed information about one object in the scene.

        Args:
            name: Exact object name as shown in Blender's Outliner.

        Returns the object's type, location, rotation (degrees), scale,
        dimensions, visibility, parent, assigned materials, modifiers, and for
        meshes its vertex and polygon counts.
        """
        if not name or not name.strip():
            raise ToolError("name must be a non-empty object name.")
        try:
            return bridge.request("get_object_info", {"name": name.strip()})
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

    @server.tool()
    def create_object(
        object_type: str,
        name: str | None = None,
        size: float = 2.0,
        location: list[float] | None = None,
        light_type: str | None = None,
    ) -> dict[str, Any]:
        """Add a new object to the scene and return its name and position.

        Args:
            object_type: One of cube, sphere, cylinder, plane, cone, torus,
                camera, light.
            name: Optional name for the new object. Blender assigns one if omitted.
            size: Overall size in Blender units. Interpreted as edge length for
                cubes and planes, and as diameter for spheres, cylinders, cones,
                and tori. Must be greater than 0.
            location: Optional [x, y, z] world position. Defaults to the origin.
            light_type: Only for object_type="light". One of POINT, SUN, SPOT,
                AREA. Defaults to POINT.
        """
        if size is not None and size <= 0:
            raise ToolError(f"size must be greater than 0, got {size}.")
        if location is not None and len(location) != 3:
            raise ToolError(
                f"location must be exactly 3 numbers [x, y, z], got {len(location)}."
            )

        params: dict[str, Any] = {"object_type": object_type, "size": size}
        if name:
            params["name"] = name
        if location is not None:
            params["location"] = [float(value) for value in location]
        if light_type:
            params["light_type"] = light_type

        try:
            return bridge.request("create_object", params)
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

    @server.tool()
    def transform_object(
        name: str,
        location: list[float] | None = None,
        rotation: list[float] | None = None,
        scale: list[float] | None = None,
    ) -> dict[str, Any]:
        """Move, rotate, or resize an existing object.

        Provide at least one of location, rotation, or scale; omitted values are
        left untouched.

        Args:
            name: Exact object name as shown in Blender's Outliner.
            location: New [x, y, z] world position.
            rotation: New [x, y, z] rotation in degrees.
            scale: New [x, y, z] scale factors. Every value must be positive.
        """
        if location is None and rotation is None and scale is None:
            raise ToolError("Provide at least one of location, rotation, or scale.")
        for label, value in (("location", location), ("rotation", rotation), ("scale", scale)):
            if value is not None and len(value) != 3:
                raise ToolError(
                    f"{label} must be exactly 3 numbers [x, y, z], got {len(value)}."
                )
        if scale is not None and any(value <= 0 for value in scale):
            raise ToolError(f"scale values must all be positive, got {scale}.")

        params: dict[str, Any] = {"name": name}
        if location is not None:
            params["location"] = [float(value) for value in location]
        if rotation is not None:
            params["rotation"] = [float(value) for value in rotation]
        if scale is not None:
            params["scale"] = [float(value) for value in scale]

        try:
            return bridge.request("transform_object", params)
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

    @server.tool()
    def delete_object(name: str) -> dict[str, Any]:
        """Permanently delete an object from the scene.

        This cannot be undone from the MCP side, so confirm the name first with
        get_scene_info or get_object_info.

        Args:
            name: Exact object name as shown in Blender's Outliner.
        """
        if not name or not name.strip():
            raise ToolError("name must be a non-empty object name.")
        try:
            return bridge.request("delete_object", {"name": name.strip()})
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

    @server.tool()
    def set_material(
        name: str,
        base_color: list[float] | None = None,
        metallic: float = 0.0,
        roughness: float = 0.5,
        material_name: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a material and assign it to a mesh object.

        Args:
            name: Exact name of the mesh object to shade.
            base_color: [r, g, b], each 0.0-1.0. (1, 0, 0) is red, (0.8, 0.8, 0.8)
                a light grey.
            metallic: 0.0 for a matte surface, 1.0 for metal. Must be 0.0-1.0.
            roughness: 0.0 for a mirror finish, 1.0 for fully diffuse. Must be 0.0-1.0.
            material_name: Optional material name. Reusing a name updates that
                material everywhere it is used.
        """
        color = None
        try:
            color = _validate_color(base_color)
        except InvalidInputError as exc:
            raise ToolError(str(exc)) from exc

        for label, value in (("metallic", metallic), ("roughness", roughness)):
            if not 0.0 <= value <= 1.0:
                raise ToolError(f"{label} must be between 0.0 and 1.0, got {value}.")

        params: dict[str, Any] = {
            "name": name,
            "metallic": metallic,
            "roughness": roughness,
        }
        if color is not None:
            params["base_color"] = color
        if material_name:
            params["material_name"] = material_name

        try:
            return bridge.request("set_material", params)
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

    @server.tool()
    def render_scene(
        filepath: str | None = None,
        resolution_x: int | None = None,
        resolution_y: int | None = None,
    ) -> dict[str, Any]:
        """Render the current scene to an image file.

        Rendering is slow: it can take seconds to minutes depending on the scene
        and engine, and Blender's window is unresponsive while it runs.

        Args:
            filepath: Where to write the image. Defaults to a temporary PNG.
            resolution_x: Output width in pixels. Omit to keep the scene setting.
            resolution_y: Output height in pixels. Omit to keep the scene setting.

        Returns the written file path, its size, the resolution, and the engine.
        The image itself is not returned; use get_viewport_screenshot for pixels.
        """
        for label, value in (("resolution_x", resolution_x), ("resolution_y", resolution_y)):
            if value is not None and value <= 0:
                raise ToolError(f"{label} must be a positive pixel count, got {value}.")

        params: dict[str, Any] = {}
        if filepath:
            params["filepath"] = filepath
        if resolution_x is not None:
            params["resolution_x"] = resolution_x
        if resolution_y is not None:
            params["resolution_y"] = resolution_y

        try:
            return bridge.request("render_scene", params)
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

    @server.tool()
    def get_viewport_screenshot(max_size: int = 800) -> types.ImageContent:
        """Capture the current Blender viewport as a PNG image.

        Use this to actually *see* the scene, which is far more reliable than
        reasoning about coordinates. Requires Blender to be running with a
        visible 3D viewport; it does not work in background mode.

        Args:
            max_size: Reserved for scaling; the viewport's own resolution is used
                and the image is returned as-is.

        Returns a PNG image that the client displays to the model.
        """
        try:
            result = bridge.request("get_viewport_screenshot", {"max_size": max_size})
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc

        encoded = result.get("image_base64")
        if not encoded:
            raise ToolError("The bridge returned no image data for the viewport screenshot.")

        raw = base64.b64decode(encoded)
        if len(raw) > MAX_IMAGE_BYTES:
            raise ToolError(
                f"The viewport screenshot is {len(raw)} bytes, above the "
                f"{MAX_IMAGE_BYTES} byte limit. Lower the viewport resolution in Blender."
            )

        return types.ImageContent(type="image", data=encoded, mimeType="image/png")
