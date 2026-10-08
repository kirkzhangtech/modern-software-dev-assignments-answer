"""Tests for the Blender add-on's command handlers.

The add-on module is loaded with a fake ``bpy`` injected, so the real command
implementations run headlessly. This covers the part of the system that would
otherwise only be verifiable by hand inside Blender.
"""

from __future__ import annotations

import base64

import pytest


class TestDispatchEnvelope:
    def test_ping_reports_available_commands(self, addon):
        response = addon.dispatch({"type": "ping"})

        assert response["status"] == "success"
        assert response["result"]["pong"] is True
        assert "create_object" in response["result"]["commands"]

    def test_every_registered_command_is_reachable(self, addon):
        for command in addon.HANDLERS:
            response = addon.dispatch({"type": command, "params": {}})
            # Some commands legitimately fail on an empty scene, but none may
            # report "unknown_command" or crash the dispatcher.
            if response["status"] == "error":
                assert response["error"]["code"] != "unknown_command"
                assert response["error"]["code"] != "internal_error"

    def test_non_object_request_is_rejected(self, addon):
        response = addon.dispatch(["not", "an", "object"])

        assert response["status"] == "error"
        assert response["error"]["code"] == "invalid_request"

    def test_missing_type_is_rejected_with_available_commands(self, addon):
        response = addon.dispatch({})

        assert response["error"]["code"] == "invalid_request"
        assert "Available commands" in response["error"]["hint"]

    def test_unknown_command_lists_the_valid_ones(self, addon):
        response = addon.dispatch({"type": "make_coffee"})

        assert response["error"]["code"] == "unknown_command"
        assert "make_coffee" in response["error"]["message"]
        assert "get_scene_info" in response["error"]["hint"]

    def test_params_must_be_an_object(self, addon):
        response = addon.dispatch({"type": "ping", "params": "nope"})

        assert response["error"]["code"] == "invalid_request"

    def test_command_alias_key_is_accepted(self, addon):
        """Older clients may send 'command' instead of 'type'."""
        assert addon.dispatch({"command": "ping"})["status"] == "success"

    def test_dispatcher_never_raises(self, addon, monkeypatch):
        def explode(params):
            raise RuntimeError("catastrophic failure")

        monkeypatch.setitem(addon.HANDLERS, "ping", explode)

        response = addon.dispatch({"type": "ping"})

        assert response["status"] == "error"
        assert response["error"]["code"] == "internal_error"
        assert "catastrophic failure" in response["error"]["message"]


class TestSceneInfo:
    def test_empty_scene_is_valid_not_an_error(self, addon):
        response = addon.dispatch({"type": "get_scene_info"})

        assert response["status"] == "success"
        assert response["result"]["object_count"] == 0
        assert response["result"]["objects"] == []

    def test_scene_lists_created_objects(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube", location=(1, 2, 3))

        result = addon.dispatch({"type": "get_scene_info"})["result"]

        assert result["object_count"] == 1
        assert result["objects"][0]["name"] == "Cube"
        assert result["objects"][0]["location"] == [1.0, 2.0, 3.0]


class TestObjectInfo:
    def test_mesh_details_include_counts(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube", vertices=8)

        result = addon.dispatch({"type": "get_object_info", "params": {"name": "Cube"}})[
            "result"
        ]

        assert result["type"] == "MESH"
        assert result["vertex_count"] == 8
        assert result["polygon_count"] == 6

    def test_rotation_is_reported_in_degrees(self, addon, fake_bpy):
        obj = fake_bpy.make_mesh("Cube")
        obj.rotation_euler = [0.0, 0.0, 1.5707963]

        result = addon.dispatch({"type": "get_object_info", "params": {"name": "Cube"}})[
            "result"
        ]

        assert result["rotation_euler_degrees"][2] == pytest.approx(90.0, abs=0.01)

    def test_missing_object_reports_available_names(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        response = addon.dispatch({"type": "get_object_info", "params": {"name": "ghost"}})

        assert response["error"]["code"] == "object_not_found"
        assert "Cube" in response["error"]["hint"]

    def test_missing_name_is_rejected(self, addon):
        response = addon.dispatch({"type": "get_object_info", "params": {}})

        assert response["error"]["code"] == "invalid_params"


class TestCreateObject:
    def test_cube_uses_the_requested_size(self, addon, fake_bpy, monkeypatch):
        captured = {}
        original = fake_bpy.ops.mesh.primitive_cube_add

        def spy(size=2.0, location=(0, 0, 0), **kwargs):
            captured["size"] = size
            captured["location"] = location
            return original(size=size, location=location, **kwargs)

        monkeypatch.setattr(fake_bpy.ops.mesh, "primitive_cube_add", spy)

        addon.dispatch(
            {"type": "create_object", "params": {"object_type": "cube", "size": 3.0}}
        )

        assert captured["size"] == 3.0

    def test_sphere_uses_size_as_diameter(self, addon, fake_bpy, monkeypatch):
        captured = {}
        original = fake_bpy.ops.mesh.primitive_uv_sphere_add

        def spy(radius=1.0, location=(0, 0, 0), **kwargs):
            captured["radius"] = radius
            return original(radius=radius, location=location, **kwargs)

        monkeypatch.setattr(fake_bpy.ops.mesh, "primitive_uv_sphere_add", spy)

        addon.dispatch(
            {"type": "create_object", "params": {"object_type": "sphere", "size": 4.0}}
        )

        assert captured["radius"] == 2.0

    def test_created_object_is_renamed(self, addon):
        result = addon.dispatch(
            {"type": "create_object", "params": {"object_type": "cube", "name": "Hero"}}
        )["result"]

        assert result["name"] == "Hero"

    def test_default_name_is_used_when_none_given(self, addon):
        result = addon.dispatch({"type": "create_object", "params": {"object_type": "cube"}})[
            "result"
        ]

        assert result["name"].startswith("Cube")

    def test_light_type_is_honoured(self, addon):
        result = addon.dispatch(
            {
                "type": "create_object",
                "params": {"object_type": "light", "light_type": "sun"},
            }
        )["result"]

        assert result["object_type"] == "LIGHT"

    def test_invalid_light_type_is_rejected(self, addon):
        response = addon.dispatch(
            {"type": "create_object", "params": {"object_type": "light", "light_type": "laser"}}
        )

        assert response["error"]["code"] == "unsupported_light_type"
        # The light type is normalised to upper case in the message.
        assert "laser" in response["error"]["message"].lower()
        assert "POINT" in response["error"]["hint"]

    def test_unsupported_object_type_lists_options(self, addon):
        response = addon.dispatch(
            {"type": "create_object", "params": {"object_type": "teapot"}}
        )

        assert response["error"]["code"] == "unsupported_object_type"
        assert "torus" in response["error"]["hint"]

    @pytest.mark.parametrize("size", [0, -5])
    def test_size_must_be_positive(self, addon, size):
        response = addon.dispatch(
            {"type": "create_object", "params": {"object_type": "cube", "size": size}}
        )

        assert response["error"]["code"] == "invalid_params"

    def test_size_must_be_a_number(self, addon):
        response = addon.dispatch(
            {"type": "create_object", "params": {"object_type": "cube", "size": "big"}}
        )

        assert response["error"]["code"] == "invalid_params"

    def test_location_must_have_three_components(self, addon):
        response = addon.dispatch(
            {"type": "create_object", "params": {"object_type": "cube", "location": [1, 2]}}
        )

        assert response["error"]["code"] == "invalid_params"


class TestTransform:
    def test_location_is_applied(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        result = addon.dispatch(
            {"type": "transform_object", "params": {"name": "Cube", "location": [4, 5, 6]}}
        )["result"]

        assert result["location"] == [4.0, 5.0, 6.0]

    def test_degrees_are_converted_to_radians(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        addon.dispatch(
            {"type": "transform_object", "params": {"name": "Cube", "rotation": [0, 0, 90]}}
        )

        stored = fake_bpy.data.objects.get("Cube").rotation_euler
        assert stored[2] == pytest.approx(1.5707963, abs=1e-6)

    def test_scale_is_applied(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        result = addon.dispatch(
            {"type": "transform_object", "params": {"name": "Cube", "scale": [2, 2, 2]}}
        )["result"]

        assert result["scale"] == [2.0, 2.0, 2.0]

    def test_no_fields_is_rejected(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        response = addon.dispatch({"type": "transform_object", "params": {"name": "Cube"}})

        assert response["error"]["code"] == "invalid_params"
        assert "at least one" in response["error"]["message"]

    def test_non_positive_scale_is_rejected(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        response = addon.dispatch(
            {"type": "transform_object", "params": {"name": "Cube", "scale": [1, -1, 1]}}
        )

        assert response["error"]["code"] == "invalid_params"

    def test_untouched_axes_keep_their_values(self, addon, fake_bpy):
        obj = fake_bpy.make_mesh("Cube")
        obj.scale = [3.0, 3.0, 3.0]

        addon.dispatch(
            {"type": "transform_object", "params": {"name": "Cube", "location": [1, 1, 1]}}
        )

        assert list(obj.scale) == [3.0, 3.0, 3.0]

    def test_missing_object_is_reported(self, addon):
        response = addon.dispatch(
            {"type": "transform_object", "params": {"name": "ghost", "location": [0, 0, 0]}}
        )

        assert response["error"]["code"] == "object_not_found"


class TestDelete:
    def test_object_is_removed(self, addon, fake_bpy):
        fake_bpy.make_mesh("Doomed")

        result = addon.dispatch({"type": "delete_object", "params": {"name": "Doomed"}})[
            "result"
        ]

        assert result["deleted"] == "Doomed"
        assert fake_bpy.data.objects.get("Doomed") is None

    def test_missing_object_is_reported(self, addon):
        response = addon.dispatch({"type": "delete_object", "params": {"name": "ghost"}})

        assert response["error"]["code"] == "object_not_found"


class TestMaterial:
    def _principled(self, fake_bpy, material_name):
        material = fake_bpy.data.materials.get(material_name)
        return next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")

    def test_material_is_created_and_assigned(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        result = addon.dispatch(
            {"type": "set_material", "params": {"name": "Cube", "base_color": [1, 0, 0]}}
        )["result"]

        assert result["material"] == "Cube_material"
        assert fake_bpy.data.objects.get("Cube").material_slots[0].material.name == "Cube_material"

    def test_base_color_reaches_the_shader(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        addon.dispatch(
            {"type": "set_material", "params": {"name": "Cube", "base_color": [0.1, 0.2, 0.3]}}
        )

        socket = self._principled(fake_bpy, "Cube_material").inputs.get("Base Color")
        assert socket.default_value == (0.1, 0.2, 0.3, 1.0)

    def test_metallic_and_roughness_reach_the_shader(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        addon.dispatch(
            {
                "type": "set_material",
                "params": {"name": "Cube", "metallic": 0.9, "roughness": 0.1},
            }
        )

        node = self._principled(fake_bpy, "Cube_material")
        assert node.inputs.get("Metallic").default_value == 0.9
        assert node.inputs.get("Roughness").default_value == 0.1

    def test_custom_material_name_is_used(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        result = addon.dispatch(
            {"type": "set_material", "params": {"name": "Cube", "material_name": "Chrome"}}
        )["result"]

        assert result["material"] == "Chrome"

    def test_existing_material_is_reused_not_duplicated(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")
        fake_bpy.data.materials.new("Paint")

        addon.dispatch(
            {"type": "set_material", "params": {"name": "Cube", "material_name": "Paint"}}
        )

        assert fake_bpy.data.materials.get("Paint") is not None

    def test_reassigning_replaces_the_first_slot(self, addon, fake_bpy):
        fake_bpy.make_mesh("Cube")

        addon.dispatch({"type": "set_material", "params": {"name": "Cube", "material_name": "A"}})
        addon.dispatch({"type": "set_material", "params": {"name": "Cube", "material_name": "B"}})

        obj = fake_bpy.data.objects.get("Cube")
        assert len(obj.material_slots) == 1
        assert obj.material_slots[0].material.name == "B"

    @pytest.mark.parametrize("component", [1.5, -0.2])
    def test_out_of_range_colour_is_rejected(self, addon, fake_bpy, component):
        fake_bpy.make_mesh("Cube")

        response = addon.dispatch(
            {
                "type": "set_material",
                "params": {"name": "Cube", "base_color": [component, 0.5, 0.5]},
            }
        )

        assert response["error"]["code"] == "invalid_params"

    @pytest.mark.parametrize("value", [1.5, -0.5])
    def test_out_of_range_metallic_is_rejected(self, addon, fake_bpy, value):
        fake_bpy.make_mesh("Cube")

        response = addon.dispatch(
            {"type": "set_material", "params": {"name": "Cube", "metallic": value}}
        )

        assert response["error"]["code"] == "invalid_params"

    def test_material_on_a_camera_is_rejected(self, addon, fake_bpy):
        fake_bpy.add_object("Cam", "CAMERA")

        response = addon.dispatch({"type": "set_material", "params": {"name": "Cam"}})

        assert response["error"]["code"] == "unsupported_object_type"


class TestRender:
    def test_render_writes_a_file(self, addon, fake_bpy, tmp_path):
        fake_bpy.scene.camera = fake_bpy.add_object("Cam", "CAMERA")
        target = tmp_path / "out.png"

        result = addon.dispatch(
            {"type": "render_scene", "params": {"filepath": str(target)}}
        )["result"]

        assert target.exists()
        assert result["size_bytes"] > 0

    def test_missing_camera_is_explained(self, addon, fake_bpy):
        response = addon.dispatch({"type": "render_scene", "params": {}})

        assert response["error"]["code"] == "no_camera"
        assert "create_object" in response["error"]["hint"]

    def test_resolution_is_applied(self, addon, fake_bpy, tmp_path):
        fake_bpy.scene.camera = fake_bpy.add_object("Cam", "CAMERA")

        result = addon.dispatch(
            {
                "type": "render_scene",
                "params": {
                    "filepath": str(tmp_path / "r.png"),
                    "resolution_x": 320,
                    "resolution_y": 240,
                },
            }
        )["result"]

        assert result["resolution"] == [320, 240]

    def test_invalid_engine_is_rejected_before_rendering(self, addon, fake_bpy, tmp_path):
        fake_bpy.scene.camera = fake_bpy.add_object("Cam", "CAMERA")

        response = addon.dispatch(
            {"type": "render_scene", "params": {"engine": "RENDERMAN"}}
        )

        assert response["error"]["code"] == "unsupported_engine"
        assert "CYCLES" in response["error"]["hint"]

    def test_valid_engine_is_accepted(self, addon, fake_bpy, tmp_path):
        fake_bpy.scene.camera = fake_bpy.add_object("Cam", "CAMERA")

        result = addon.dispatch(
            {
                "type": "render_scene",
                "params": {"filepath": str(tmp_path / "e.png"), "engine": "CYCLES"},
            }
        )["result"]

        assert result["engine"] == "CYCLES"

    def test_default_filepath_gets_a_png_extension(self, addon, fake_bpy, tmp_path, monkeypatch):
        fake_bpy.scene.camera = fake_bpy.add_object("Cam", "CAMERA")
        monkeypatch.setattr(addon.tempfile, "gettempdir", lambda: str(tmp_path))

        result = addon.dispatch({"type": "render_scene", "params": {}})["result"]

        assert result["filepath"].endswith(".png")


class TestViewportScreenshot:
    def test_screenshot_returns_base64_png(self, addon, fake_bpy, tmp_path, monkeypatch):
        monkeypatch.setattr(addon.tempfile, "gettempdir", lambda: str(tmp_path))

        result = addon.dispatch({"type": "get_viewport_screenshot", "params": {}})["result"]

        assert result["mime_type"] == "image/png"
        assert base64.b64decode(result["image_base64"]).startswith(b"\x89PNG")

    def test_background_mode_failure_explains_itself(self, addon, fake_bpy):
        addon.executor = addon.InlineExecutor()
        fake_bpy.fail_opengl = True

        response = addon.dispatch({"type": "get_viewport_screenshot", "params": {}})

        assert response["error"]["code"] == "viewport_unavailable"
        assert "background mode" in response["error"]["hint"]

    def test_render_filepath_is_restored_afterwards(self, addon, fake_bpy, tmp_path, monkeypatch):
        monkeypatch.setattr(addon.tempfile, "gettempdir", lambda: str(tmp_path))
        fake_bpy.scene.render.filepath = "/original/path.png"

        addon.dispatch({"type": "get_viewport_screenshot", "params": {}})

        assert fake_bpy.scene.render.filepath == "/original/path.png"


class TestMainThreadExecution:
    def test_tasks_run_on_the_blender_timer(self, addon, fake_bpy):
        executor = addon.MainThreadExecutor()
        executor.ensure_started()

        assert fake_bpy.timers_registered, "the executor must register a timer"

    def test_timer_drain_executes_queued_work(self, addon, fake_bpy):
        executor = addon.MainThreadExecutor()
        executor.ensure_started()
        drain = fake_bpy.timers_registered[-1]

        import threading

        box = {}

        def worker():
            try:
                box["value"] = executor.submit(lambda: 6 * 7, timeout=5.0)
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        for _ in range(400):
            drain()
            if box:
                break
            threading.Event().wait(0.005)

        thread.join(timeout=5)
        assert box.get("error") is None
        assert box.get("value") == 42

    def test_drain_returns_the_reschedule_interval(self, addon, fake_bpy):
        executor = addon.MainThreadExecutor(interval=0.25)

        assert executor._drain() == 0.25

    def test_executor_starts_only_one_timer(self, addon, fake_bpy):
        executor = addon.MainThreadExecutor()
        executor.ensure_started()
        executor.ensure_started()

        assert len(fake_bpy.timers_registered) == 1

    def test_exceptions_cross_the_thread_boundary(self, addon, fake_bpy):
        executor = addon.MainThreadExecutor()
        executor.ensure_started()
        drain = fake_bpy.timers_registered[-1]

        import threading

        box = {}

        def worker():
            try:
                executor.submit(lambda: (_ for _ in ()).throw(ValueError("nope")), timeout=5.0)
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        for _ in range(400):
            drain()
            if box:
                break
            threading.Event().wait(0.005)

        thread.join(timeout=5)
        assert isinstance(box.get("error"), ValueError)

    def test_timeout_is_reported_when_the_timer_never_runs(self, addon):
        executor = addon.MainThreadExecutor()
        executor.ensure_started()

        with pytest.raises(addon.CommandError) as excinfo:
            executor.submit(lambda: "never returned", timeout=0.05)

        assert excinfo.value.code == "timeout"
        assert "modal dialog" in excinfo.value.hint


class TestSocketServer:
    def test_bridge_serves_a_request_over_tcp(self, addon, fake_bpy, fake_blender_bridge_free_port):
        import json
        import socket

        server = addon.BlenderBridgeServer(port=fake_blender_bridge_free_port)
        host, port = server.start()
        try:
            with socket.create_connection((host, port), timeout=5) as connection:
                connection.sendall(json.dumps({"type": "ping"}).encode() + b"\n")
                raw = connection.makefile("rb").readline()

            response = json.loads(raw)
            assert response["status"] == "success"
            assert response["result"]["pong"] is True
        finally:
            server.stop()

    def test_malformed_json_gets_an_error_not_a_crash(self, addon, fake_blender_bridge_free_port):
        import json
        import socket

        server = addon.BlenderBridgeServer(port=fake_blender_bridge_free_port)
        host, port = server.start()
        try:
            with socket.create_connection((host, port), timeout=5) as connection:
                connection.sendall(b"{not json}\n")
                raw = connection.makefile("rb").readline()

            response = json.loads(raw)
            assert response["status"] == "error"
            assert response["error"]["code"] == "invalid_json"
        finally:
            server.stop()

    def test_starting_twice_is_a_no_op(self, addon, fake_blender_bridge_free_port):
        server = addon.BlenderBridgeServer(port=fake_blender_bridge_free_port)
        first = server.start()
        second = server.start()
        try:
            assert first == second
        finally:
            server.stop()

    def test_unbindable_address_is_reported(self, addon):
        # Windows allows two sockets to share a port when SO_REUSEADDR is set, so
        # a bind conflict is not portable to test. An unresolvable host is.
        server = addon.BlenderBridgeServer(host="no-such-host.invalid", port=9876)

        with pytest.raises(addon.CommandError) as excinfo:
            server.start()

        assert excinfo.value.code == "port_unavailable"

    def test_stop_is_idempotent(self, addon, fake_blender_bridge_free_port):
        server = addon.BlenderBridgeServer(port=fake_blender_bridge_free_port)
        server.start()
        server.stop()
        server.stop()

        assert server.is_running is False


class TestAddonRegistration:
    def test_register_wires_every_class(self, addon, fake_bpy):
        addon.register()

        assert len(fake_bpy.registered_classes) == len(addon._CLASSES)

    def test_unregister_stops_the_bridge(self, addon, fake_bpy, monkeypatch):
        stopped = {"called": False}
        monkeypatch.setattr(
            addon.bridge_server, "stop", lambda: stopped.update(called=True)
        )

        addon.unregister()

        assert stopped["called"] is True

    def test_expected_handlers_are_registered(self, addon):
        assert set(addon.HANDLERS) == {
            "ping",
            "get_scene_info",
            "get_object_info",
            "create_object",
            "delete_object",
            "transform_object",
            "set_material",
            "render_scene",
            "get_viewport_screenshot",
        }
