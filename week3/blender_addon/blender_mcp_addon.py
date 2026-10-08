"""Blender MCP bridge addon.

Runs **inside** Blender and exposes a small newline-delimited JSON protocol over
a local TCP socket. An external MCP server (``week3/server``) connects to this
socket and drives Blender through it.

Why a socket bridge instead of importing ``bpy`` directly?

* ``bpy`` only exists inside a running Blender process.
* Driving the real application means scripted edits happen in the same session a
  human is looking at, and things like viewport screenshots are possible.

Threading model
---------------
Blender's Python API is **not thread-safe** and must only be called from the main
thread. Socket connections therefore arrive on worker threads, and each command
is handed to :class:`MainThreadExecutor`, which uses ``bpy.app.timers`` to run it
on the main thread while the worker blocks on the result. See
:func:`dispatch`.

Installation
------------
Blender > Edit > Preferences > Add-ons > Install from Disk... > select this file,
then enable "Blender MCP Bridge". Start and stop the server from the 3D viewport
sidebar (press ``N``) under the "Blender MCP" tab.
"""

from __future__ import annotations

bl_info = {
    "name": "Blender MCP Bridge",
    "author": "Week 3 Assignment",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Blender MCP",
    "description": "Exposes a local TCP bridge so an external MCP server can drive Blender.",
    "category": "Interface",
}

import base64
import json
import logging
import math
import os
import queue
import socketserver
import sys
import tempfile
import threading

import bpy

# --------------------------------------------------------------------------- #
# Logging
#
# Never use print(): a stray write to stdout would corrupt any stdio-based
# protocol attached to this process, and Blender's console already shows stderr.
# --------------------------------------------------------------------------- #

logger = logging.getLogger("blender_mcp_bridge")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[blender-mcp] %(levelname)s %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
DEFAULT_COMMAND_TIMEOUT = 30.0
MAX_REQUEST_BYTES = 1_000_000


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class CommandError(Exception):
    """A command failed in a way the caller can act on."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload


# --------------------------------------------------------------------------- #
# Main-thread execution
# --------------------------------------------------------------------------- #


class _Task:
    __slots__ = ("func", "result", "error", "done")

    def __init__(self, func) -> None:
        self.func = func
        self.result = None
        self.error: BaseException | None = None
        self.done = threading.Event()


class InlineExecutor:
    """Runs tasks on the calling thread.

    Used by tests, and as the fallback when Blender's timer system is not
    available (for example when the command logic is exercised headlessly).
    """

    def submit(self, func, timeout: float | None = None):
        return func()


class MainThreadExecutor:
    """Runs tasks on Blender's main thread via ``bpy.app.timers``."""

    def __init__(self, interval: float = 0.02) -> None:
        self._interval = interval
        self._queue: queue.Queue[_Task] = queue.Queue()
        self._lock = threading.Lock()
        self._registered = False

    def ensure_started(self) -> None:
        with self._lock:
            if self._registered:
                return
            bpy.app.timers.register(self._drain, first_interval=self._interval)
            self._registered = True
            logger.info("Main-thread executor started")

    def _drain(self) -> float:
        """Execute every queued task. Returning a float reschedules the timer."""
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            if task.done.is_set():
                continue
            try:
                task.result = task.func()
            except BaseException as exc:  # noqa: BLE001 - relayed to the caller
                task.error = exc
            finally:
                task.done.set()
        return self._interval

    def submit(self, func, timeout: float | None = DEFAULT_COMMAND_TIMEOUT):
        self.ensure_started()
        task = _Task(func)
        self._queue.put(task)
        if not task.done.wait(timeout):
            raise CommandError(
                "timeout",
                f"Blender did not finish the command within {timeout:g}s.",
                "Blender may be busy or waiting on a modal dialog. "
                "Dismiss any open dialog and retry.",
            )
        if task.error is not None:
            raise task.error
        return task.result


# Swapped for an InlineExecutor in headless tests.
executor: MainThreadExecutor | InlineExecutor = MainThreadExecutor()


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _require_str(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandError(
            "invalid_params",
            f"Parameter '{key}' is required and must be a non-empty string.",
        )
    return value.strip()


def _vec3(value, key: str, default=None) -> tuple[float, float, float]:
    if value is None:
        if default is None:
            raise CommandError("invalid_params", f"Parameter '{key}' is required.")
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise CommandError("invalid_params", f"Parameter '{key}' must be a list of 3 numbers.")
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise CommandError("invalid_params", f"Parameter '{key}' must contain numbers.") from exc


def _bounded_float(params: dict, key: str, default: float, low: float, high: float) -> float:
    raw = params.get(key, default)
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise CommandError("invalid_params", f"Parameter '{key}' must be a number.") from exc
    if not low <= number <= high:
        raise CommandError(
            "invalid_params", f"Parameter '{key}' must be between {low} and {high}, got {number}."
        )
    return number


def _get_object(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None:
        available = sorted(o.name for o in bpy.data.objects)[:20]
        raise CommandError(
            "object_not_found",
            f"No object named '{name}' exists in this blend file.",
            f"Available objects: {available}" if available else "The scene has no objects yet.",
        )
    return obj


def _vec(value) -> list[float]:
    return [round(float(component), 6) for component in value]


def _vec_degrees(value) -> list[float]:
    return [round(math.degrees(float(component)), 4) for component in value]


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_ping(params: dict) -> dict:
    return {
        "pong": True,
        "blender_version": bpy.app.version_string,
        "commands": sorted(HANDLERS),
    }


def cmd_get_scene_info(params: dict) -> dict:
    scene = bpy.context.scene
    objects = [
        {"name": obj.name, "type": obj.type, "location": _vec(obj.location)}
        for obj in scene.objects
    ]
    return {
        "scene": scene.name,
        "blender_version": bpy.app.version_string,
        "filepath": bpy.data.filepath or None,
        "frame_current": scene.frame_current,
        "object_count": len(objects),
        "objects": objects,
        "active_object": (bpy.context.active_object.name if bpy.context.active_object else None),
    }


def cmd_get_object_info(params: dict) -> dict:
    obj = _get_object(_require_str(params, "name"))
    info = {
        "name": obj.name,
        "type": obj.type,
        "location": _vec(obj.location),
        "rotation_euler_degrees": _vec_degrees(obj.rotation_euler),
        "scale": _vec(obj.scale),
        "dimensions": _vec(obj.dimensions),
        "visible": not obj.hide_viewport,
        "parent": obj.parent.name if obj.parent else None,
        "materials": [slot.material.name for slot in obj.material_slots if slot.material],
        "modifiers": [modifier.name for modifier in obj.modifiers],
    }
    data = getattr(obj, "data", None)
    if obj.type == "MESH" and data is not None:
        info["vertex_count"] = len(data.vertices)
        info["polygon_count"] = len(data.polygons)
    elif obj.type in {"CAMERA", "LIGHT"} and data is not None:
        info["data_type"] = data.type if hasattr(data, "type") else None
    return info


def _add_cube(size: float, location, params: dict):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)


def _add_uv_sphere(size: float, location, params: dict):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=size / 2.0, location=location)


def _add_cylinder(size: float, location, params: dict):
    bpy.ops.mesh.primitive_cylinder_add(radius=size / 2.0, depth=size, location=location)


def _add_plane(size: float, location, params: dict):
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)


def _add_cone(size: float, location, params: dict):
    bpy.ops.mesh.primitive_cone_add(radius1=size / 2.0, depth=size, location=location)


def _add_torus(size: float, location, params: dict):
    bpy.ops.mesh.primitive_torus_add(
        major_radius=size / 2.0, minor_radius=size / 8.0, location=location
    )


def _add_camera(size: float, location, params: dict):
    bpy.ops.object.camera_add(location=location)


def _add_light(size: float, location, params: dict):
    light_type = str(params.get("light_type", "POINT")).strip().upper()
    if light_type not in SUPPORTED_LIGHT_TYPES:
        raise CommandError(
            "unsupported_light_type",
            f"'{light_type}' is not a supported light type.",
            f"Supported light types: {sorted(SUPPORTED_LIGHT_TYPES)}",
        )
    bpy.ops.object.light_add(type=light_type, location=location)


SUPPORTED_LIGHT_TYPES = {"POINT", "SUN", "SPOT", "AREA"}

# Each builder takes (size, location, params) so type-specific options such as
# light_type can be read without special-casing inside create_object.
PRIMITIVE_BUILDERS = {
    "cube": _add_cube,
    "sphere": _add_uv_sphere,
    "cylinder": _add_cylinder,
    "plane": _add_plane,
    "cone": _add_cone,
    "torus": _add_torus,
    "camera": _add_camera,
    "light": _add_light,
}


def cmd_create_object(params: dict) -> dict:
    kind = _require_str(params, "object_type").lower()
    builder = PRIMITIVE_BUILDERS.get(kind)
    if builder is None:
        raise CommandError(
            "unsupported_object_type",
            f"Cannot create an object of type '{kind}'.",
            f"Supported types: {sorted(PRIMITIVE_BUILDERS)}",
        )

    size = _bounded_float(params, "size", default=2.0, low=1e-4, high=10_000.0)
    location = _vec3(params.get("location"), "location", default=(0.0, 0.0, 0.0))

    builder(size, location, params)

    obj = bpy.context.active_object
    if obj is None:
        raise CommandError(
            "creation_failed",
            f"Blender did not create a '{kind}' object.",
            "The active view layer may be empty or the operation was cancelled.",
        )

    requested_name = params.get("name")
    if isinstance(requested_name, str) and requested_name.strip():
        obj.name = requested_name.strip()

    logger.info("Created %s '%s' at %s", kind, obj.name, location)
    return {
        "name": obj.name,
        "object_type": obj.type,
        "location": _vec(obj.location),
        "dimensions": _vec(obj.dimensions),
    }


def cmd_delete_object(params: dict) -> dict:
    obj = _get_object(_require_str(params, "name"))
    name = obj.name
    bpy.data.objects.remove(obj, do_unlink=True)
    logger.info("Deleted object '%s'", name)
    return {"deleted": name}


def cmd_transform_object(params: dict) -> dict:
    obj = _get_object(_require_str(params, "name"))

    if not any(key in params for key in ("location", "rotation", "scale")):
        raise CommandError(
            "invalid_params",
            "Provide at least one of 'location', 'rotation', or 'scale'.",
        )

    if "location" in params:
        obj.location = _vec3(params.get("location"), "location")
    if "rotation" in params:
        degrees = _vec3(params.get("rotation"), "rotation")
        obj.rotation_euler = tuple(math.radians(value) for value in degrees)
    if "scale" in params:
        scale = _vec3(params.get("scale"), "scale")
        if any(component <= 0 for component in scale):
            raise CommandError("invalid_params", "Parameter 'scale' must be positive on every axis.")
        obj.scale = scale

    logger.info("Transformed object '%s'", obj.name)
    return {
        "name": obj.name,
        "location": _vec(obj.location),
        "rotation_euler_degrees": _vec_degrees(obj.rotation_euler),
        "scale": _vec(obj.scale),
    }


def cmd_set_material(params: dict) -> dict:
    obj = _get_object(_require_str(params, "name"))

    color = _vec3(params.get("base_color", [0.8, 0.8, 0.8]), "base_color")
    for component in color:
        if not 0.0 <= component <= 1.0:
            raise CommandError(
                "invalid_params",
                "Parameter 'base_color' components must be between 0.0 and 1.0.",
            )
    metallic = _bounded_float(params, "metallic", default=0.0, low=0.0, high=1.0)
    roughness = _bounded_float(params, "roughness", default=0.5, low=0.0, high=1.0)

    material_name = params.get("material_name")
    if not isinstance(material_name, str) or not material_name.strip():
        material_name = f"{obj.name}_material"
    else:
        material_name = material_name.strip()

    material = bpy.data.materials.get(material_name) or bpy.data.materials.new(material_name)
    material.use_nodes = True

    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        raise CommandError(
            "material_setup_failed", "The material has no node tree to configure."
        )

    principled = next(
        (node for node in node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None
    )
    if principled is None:
        raise CommandError(
            "material_setup_failed",
            "The material has no Principled BSDF node.",
            "This Blender version may use a different default shader.",
        )

    def set_input(socket_name: str, value) -> None:
        socket = principled.inputs.get(socket_name)
        if socket is None:
            raise CommandError(
                "unsupported_material",
                f"The Principled BSDF in this Blender version has no '{socket_name}' input.",
            )
        socket.default_value = value

    set_input("Base Color", (color[0], color[1], color[2], 1.0))
    set_input("Metallic", metallic)
    set_input("Roughness", roughness)

    if obj.type != "MESH":
        raise CommandError(
            "unsupported_object_type",
            f"Materials can only be assigned to mesh objects, but '{obj.name}' is a {obj.type}.",
        )

    if obj.data.materials:
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)

    logger.info("Applied material '%s' to '%s'", material.name, obj.name)
    return {
        "object": obj.name,
        "material": material.name,
        "base_color": color,
        "metallic": metallic,
        "roughness": roughness,
    }


def _available_render_engines() -> list[str]:
    """Return the render engines this Blender build supports."""
    try:
        property_definition = bpy.types.RenderSettings.bl_rna.properties["engine"]
        return [item.identifier for item in property_definition.enum_items]
    except (AttributeError, KeyError, TypeError):
        return []


def cmd_render_scene(params: dict) -> dict:
    scene = bpy.context.scene
    filepath = params.get("filepath")
    if not isinstance(filepath, str) or not filepath.strip():
        filepath = os.path.join(tempfile.gettempdir(), "blender_mcp_render.png")
    filepath = filepath.strip()
    if not os.path.splitext(filepath)[1]:
        filepath += ".png"

    if "resolution_x" in params or "resolution_y" in params:
        scene.render.resolution_x = int(
            _bounded_float(params, "resolution_x", scene.render.resolution_x, 1, 32768)
        )
        scene.render.resolution_y = int(
            _bounded_float(params, "resolution_y", scene.render.resolution_y, 1, 32768)
        )

    if "engine" in params:
        engine = _require_str(params, "engine")
        available = _available_render_engines()
        if available and engine not in available:
            raise CommandError(
                "unsupported_engine",
                f"'{engine}' is not a valid render engine.",
                f"Available engines: {available}",
            )
        try:
            scene.render.engine = engine
        except TypeError as exc:
            raise CommandError(
                "unsupported_engine",
                f"'{engine}' is not a valid render engine.",
                f"Available engines: {available}",
            ) from exc

    if not scene.camera:
        raise CommandError(
            "no_camera",
            "The scene has no active camera, so it cannot be rendered.",
            "Create one with create_object(object_type='camera'), or set scene.camera in Blender.",
        )

    scene.render.filepath = filepath
    try:
        bpy.ops.render.render(write_still=True)
    except RuntimeError as exc:
        raise CommandError("render_failed", f"Blender failed to render: {exc}") from exc

    if not os.path.exists(filepath):
        raise CommandError(
            "render_failed",
            "Blender reported success but no image file was written.",
            f"Expected a file at '{filepath}'.",
        )

    logger.info("Rendered scene to %s", filepath)
    return {
        "filepath": filepath,
        "size_bytes": os.path.getsize(filepath),
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "engine": scene.render.engine,
    }


def cmd_get_viewport_screenshot(params: dict) -> dict:
    scene = bpy.context.scene

    max_size = int(_bounded_float(params, "max_size", default=800, low=64, high=4096))
    original_filepath = scene.render.filepath
    filepath = os.path.join(tempfile.gettempdir(), "blender_mcp_viewport.png")
    scene.render.filepath = filepath

    try:
        bpy.ops.render.opengl(write_still=True)
    except RuntimeError as exc:
        raise CommandError(
            "viewport_unavailable",
            f"Could not capture the viewport: {exc}",
            "OpenGL capture needs a running Blender with a 3D viewport. "
            "It is unavailable in background mode (blender --background).",
        ) from exc
    finally:
        scene.render.filepath = original_filepath

    if not os.path.exists(filepath):
        raise CommandError(
            "viewport_unavailable",
            "Blender did not write a viewport image.",
            "Make sure a 3D viewport is visible before requesting a screenshot.",
        )

    with open(filepath, "rb") as image_file:
        raw = image_file.read()

    logger.info("Captured viewport screenshot (%d bytes)", len(raw))
    return {
        "image_base64": base64.b64encode(raw).decode("ascii"),
        "mime_type": "image/png",
        "filepath": filepath,
        "size_bytes": len(raw),
        "max_size": max_size,
    }


HANDLERS = {
    "ping": cmd_ping,
    "get_scene_info": cmd_get_scene_info,
    "get_object_info": cmd_get_object_info,
    "create_object": cmd_create_object,
    "delete_object": cmd_delete_object,
    "transform_object": cmd_transform_object,
    "set_material": cmd_set_material,
    "render_scene": cmd_render_scene,
    "get_viewport_screenshot": cmd_get_viewport_screenshot,
}


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def _error_response(code: str, message: str, hint: str | None = None) -> dict:
    payload = {"code": code, "message": message}
    if hint:
        payload["hint"] = hint
    return {"status": "error", "error": payload}


def dispatch(request) -> dict:
    """Validate and run one request, always returning a response dict.

    Never raises: every failure is converted into a structured error response so
    a single bad request cannot take the bridge down.
    """
    if not isinstance(request, dict):
        return _error_response("invalid_request", "Each request must be a JSON object.")

    command = request.get("type") or request.get("command")
    if not isinstance(command, str) or not command.strip():
        return _error_response(
            "invalid_request",
            "Request is missing the 'type' field.",
            f"Available commands: {sorted(HANDLERS)}",
        )
    command = command.strip()

    handler = HANDLERS.get(command)
    if handler is None:
        return _error_response(
            "unknown_command",
            f"Unknown command '{command}'.",
            f"Available commands: {sorted(HANDLERS)}",
        )

    params = request.get("params") or {}
    if not isinstance(params, dict):
        return _error_response("invalid_request", "'params' must be a JSON object.")

    try:
        result = executor.submit(lambda: handler(params))
    except CommandError as exc:
        logger.warning("Command '%s' failed: %s", command, exc.message)
        return {"status": "error", "error": exc.to_dict()}
    except Exception as exc:  # noqa: BLE001 - must never escape into the socket loop
        logger.exception("Command '%s' raised an unexpected error", command)
        return _error_response("internal_error", f"{type(exc).__name__}: {exc}")

    return {"status": "success", "result": result}


# --------------------------------------------------------------------------- #
# Socket server
# --------------------------------------------------------------------------- #


class _RequestHandler(socketserver.StreamRequestHandler):
    """Reads one newline-delimited JSON request and writes one JSON response."""

    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST_BYTES)
        if not raw:
            return

        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            response = _error_response(
                "invalid_json", f"Could not parse the request as JSON: {exc}"
            )
        else:
            response = dispatch(request)

        try:
            self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")
        except OSError as exc:
            logger.warning("Failed to send response: %s", exc)


class BlenderBridgeServer:
    """Threaded TCP server that serves the bridge protocol."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._server is not None

    def start(self) -> tuple[str, int]:
        if self._server is not None:
            return self.host, self.port

        class _Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        try:
            self._server = _Server((self.host, self.port), _RequestHandler)
        except OSError as exc:
            raise CommandError(
                "port_unavailable",
                f"Could not listen on {self.host}:{self.port}: {exc}",
                "Another program may be using this port. Choose a different one in "
                "the add-on preferences.",
            ) from exc

        self._thread = threading.Thread(
            target=self._server.serve_forever, name="blender-mcp-bridge", daemon=True
        )
        self._thread.start()
        logger.info("Bridge listening on %s:%d", self.host, self.port)
        return self.host, self.port

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Bridge stopped")


bridge_server = BlenderBridgeServer()


# --------------------------------------------------------------------------- #
# Add-on registration
# --------------------------------------------------------------------------- #


class BLENDERMCP_OT_StartBridge(bpy.types.Operator):
    bl_idname = "blendermcp.start_bridge"
    bl_label = "Start MCP Bridge"
    bl_description = "Start accepting commands from the external MCP server"

    def execute(self, context):
        preferences = context.preferences.addons[__name__].preferences
        bridge_server.host = preferences.host
        bridge_server.port = preferences.port
        try:
            host, port = bridge_server.start()
        except CommandError as exc:
            self.report({"ERROR"}, exc.message)
            return {"CANCELLED"}
        executor.ensure_started()
        self.report({"INFO"}, f"Blender MCP bridge listening on {host}:{port}")
        return {"FINISHED"}


class BLENDERMCP_OT_StopBridge(bpy.types.Operator):
    bl_idname = "blendermcp.stop_bridge"
    bl_label = "Stop MCP Bridge"
    bl_description = "Stop accepting commands from the external MCP server"

    def execute(self, context):
        bridge_server.stop()
        self.report({"INFO"}, "Blender MCP bridge stopped")
        return {"FINISHED"}


class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_idname = "BLENDERMCP_PT_panel"
    bl_label = "Blender MCP"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Blender MCP"

    def draw(self, context):
        layout = self.layout
        preferences = context.preferences.addons[__name__].preferences

        status = "Running" if bridge_server.is_running else "Stopped"
        layout.label(text=f"Status: {status}")
        layout.label(text=f"{preferences.host}:{preferences.port}")

        row = layout.row()
        row.enabled = not bridge_server.is_running
        row.operator(BLENDERMCP_OT_StartBridge.bl_idname, icon="PLAY")

        row = layout.row()
        row.enabled = bridge_server.is_running
        row.operator(BLENDERMCP_OT_StopBridge.bl_idname, icon="PAUSE")


class BLENDERMCP_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    host: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Host",
        description="Interface the bridge listens on",
        default=DEFAULT_HOST,
    )
    port: bpy.props.IntProperty(  # type: ignore[valid-type]
        name="Port",
        description="TCP port the bridge listens on",
        default=DEFAULT_PORT,
        min=1024,
        max=65535,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "host")
        layout.prop(self, "port")


_CLASSES = (
    BLENDERMCP_OT_StartBridge,
    BLENDERMCP_OT_StopBridge,
    BLENDERMCP_PT_Panel,
    BLENDERMCP_AddonPreferences,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    logger.info("Blender MCP Bridge add-on registered")


def unregister() -> None:
    bridge_server.stop()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    logger.info("Blender MCP Bridge add-on unregistered")


if __name__ == "__main__":
    register()
