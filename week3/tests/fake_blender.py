"""A fake Blender bridge: a real TCP server that speaks the add-on protocol.

Unlike :mod:`fake_bpy`, this does not stub Python objects. It opens an actual
socket on a real port and answers with the same newline-delimited JSON envelope
the add-on uses, backed by a small in-memory scene. That makes it possible to
test the bridge's networking, retries, timeouts, and the MCP tools end to end
without installing Blender.

Failure modes are injectable so the error paths can be exercised:
``response_delay`` stalls replies, ``fail_commands`` makes specific commands
return an error envelope, and ``malformed`` makes the server send junk.
"""

from __future__ import annotations

import base64
import json
import socketserver
import threading
import time
from typing import Any, Callable

# A 1x1 transparent PNG, so image-returning tools have real bytes to decode.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

SUPPORTED_OBJECT_TYPES = {
    "cube",
    "sphere",
    "cylinder",
    "plane",
    "cone",
    "torus",
    "camera",
    "light",
}


class FakeScene:
    """A tiny object store standing in for Blender's data model."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def create(self, name: str, object_type: str, location, size: float) -> dict[str, Any]:
        record = {
            "name": name,
            "type": "MESH" if object_type not in {"CAMERA", "LIGHT"} else object_type,
            "location": list(location),
            "dimensions": [size, size, size],
            "materials": [],
        }
        self.objects[name] = record
        return record


def default_responder(scene: FakeScene) -> Callable[[dict], dict]:
    """Build a responder that implements every command against ``scene``."""

    counter = {"n": 0}

    def next_name(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}.{counter['n']:03d}"

    def handle(request: dict) -> dict:
        command = request.get("type")
        params = request.get("params") or {}

        if command == "ping":
            return {
                "status": "success",
                "result": {"pong": True, "blender_version": "4.2.0", "commands": ["ping"]},
            }

        if command == "get_scene_info":
            return {
                "status": "success",
                "result": {
                    "scene": "Scene",
                    "blender_version": "4.2.0",
                    "filepath": "/tmp/demo.blend",
                    "frame_current": 1,
                    "object_count": len(scene.objects),
                    "objects": [
                        {"name": name, "type": record["type"], "location": record["location"]}
                        for name, record in scene.objects.items()
                    ],
                    "active_object": None,
                },
            }

        if command == "get_object_info":
            name = params.get("name")
            record = scene.objects.get(name)
            if record is None:
                return {
                    "status": "error",
                    "error": {
                        "code": "object_not_found",
                        "message": f"No object named '{name}' exists in this blend file.",
                        "hint": f"Available objects: {sorted(scene.objects)}",
                    },
                }
            return {
                "status": "success",
                "result": {**record, "vertex_count": 8, "polygon_count": 6},
            }

        if command == "create_object":
            object_type = params.get("object_type", "cube")
            # Mirror the add-on's validation so unknown types fail over the wire
            # exactly as they would against real Blender.
            if object_type not in SUPPORTED_OBJECT_TYPES:
                return {
                    "status": "error",
                    "error": {
                        "code": "unsupported_object_type",
                        "message": f"Cannot create an object of type '{object_type}'.",
                        "hint": f"Supported types: {sorted(SUPPORTED_OBJECT_TYPES)}",
                    },
                }
            record = scene.create(
                params.get("name") or next_name(object_type.capitalize()),
                object_type,
                params.get("location", [0.0, 0.0, 0.0]),
                float(params.get("size", 2.0)),
            )
            return {"status": "success", "result": record}

        if command == "delete_object":
            name = params.get("name")
            if name not in scene.objects:
                return {
                    "status": "error",
                    "error": {"code": "object_not_found", "message": f"No object named '{name}'."},
                }
            del scene.objects[name]
            return {"status": "success", "result": {"deleted": name}}

        if command == "transform_object":
            name = params.get("name")
            record = scene.objects.get(name)
            if record is None:
                return {
                    "status": "error",
                    "error": {"code": "object_not_found", "message": f"No object named '{name}'."},
                }
            if "location" in params:
                record["location"] = list(params["location"])
            return {
                "status": "success",
                "result": {
                    "name": name,
                    "location": record["location"],
                    "rotation_euler_degrees": params.get("rotation", [0.0, 0.0, 0.0]),
                    "scale": params.get("scale", [1.0, 1.0, 1.0]),
                },
            }

        if command == "set_material":
            name = params.get("name")
            record = scene.objects.get(name)
            if record is None:
                return {
                    "status": "error",
                    "error": {"code": "object_not_found", "message": f"No object named '{name}'."},
                }
            material = params.get("material_name") or f"{name}_material"
            record["materials"] = [material]
            return {
                "status": "success",
                "result": {
                    "object": name,
                    "material": material,
                    "base_color": params.get("base_color", [0.8, 0.8, 0.8]),
                    "metallic": params.get("metallic", 0.0),
                    "roughness": params.get("roughness", 0.5),
                },
            }

        if command == "render_scene":
            return {
                "status": "success",
                "result": {
                    "filepath": params.get("filepath", "/tmp/blender_mcp_render.png"),
                    "size_bytes": 1024,
                    "resolution": [params.get("resolution_x", 1920), params.get("resolution_y", 1080)],
                    "engine": "BLENDER_EEVEE_NEXT",
                },
            }

        if command == "get_viewport_screenshot":
            return {
                "status": "success",
                "result": {
                    "image_base64": base64.b64encode(TINY_PNG).decode("ascii"),
                    "mime_type": "image/png",
                    "filepath": "/tmp/blender_mcp_viewport.png",
                    "size_bytes": len(TINY_PNG),
                    "max_size": params.get("max_size", 800),
                },
            }

        return {
            "status": "error",
            "error": {
                "code": "unknown_command",
                "message": f"Unknown command '{command}'.",
            },
        }

    return handle


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(1_000_000)
        if not raw:
            return

        server = self.server
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            request = {"type": "__malformed__"}

        server.requests.append(request)

        if server.response_delay:
            time.sleep(server.response_delay)

        if server.malformed:
            self.wfile.write(b"this is not json\n")
            return

        command = request.get("type")
        if command in server.fail_commands:
            response = {
                "status": "error",
                "error": {
                    "code": server.fail_commands[command],
                    "message": f"'{command}' was configured to fail.",
                },
            }
        else:
            response = server.responder(request)

        if response is None:
            # Simulate a bridge that accepts the request then hangs up without
            # answering, which the client must treat as a failure.
            return

        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeBlenderBridge:
    """A scriptable stand-in for the Blender add-on's socket server."""

    def __init__(
        self,
        responder: Callable[[dict], dict | None] | None = None,
        response_delay: float = 0.0,
        malformed: bool = False,
        fail_commands: dict[str, str] | None = None,
    ) -> None:
        self.scene = FakeScene()
        self.responder = responder or default_responder(self.scene)
        self.response_delay = response_delay
        self.malformed = malformed
        self.fail_commands = fail_commands or {}
        self.requests: list[dict] = []
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("The fake bridge is not running.")
        return self._server.server_address[1]

    def start(self) -> FakeBlenderBridge:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.responder = self.responder
        self._server.requests = self.requests
        self._server.response_delay = self.response_delay
        self._server.malformed = self.malformed
        self._server.fail_commands = self.fail_commands

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> FakeBlenderBridge:
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def commands_received(self) -> list[str]:
        return [request.get("type") for request in self.requests]


def free_port() -> int:
    """Return a port number that is currently closed, for failure tests."""
    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    server.server_close()
    return port
