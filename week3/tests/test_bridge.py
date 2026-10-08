"""Tests for the bridge client: protocol handling, retries, timeouts, errors."""

from __future__ import annotations

import pytest

from week3.server.bridge import BlenderBridge
from week3.server.errors import (
    BlenderCommandError,
    BlenderProtocolError,
    BlenderTimeoutError,
    BlenderUnavailableError,
)

from .conftest import make_settings
from .fake_blender import FakeBlenderBridge, free_port


class TestHappyPath:
    def test_ping_returns_bridge_metadata(self, bridge):
        result = bridge.ping()

        assert result["pong"] is True
        assert "blender_version" in result

    def test_request_returns_result_payload(self, bridge, fake_blender):
        result = bridge.request("get_scene_info")

        assert result["scene"] == "Scene"
        assert "objects" in result

    def test_is_available_is_true_when_bridge_responds(self, bridge):
        assert bridge.is_available() is True

    def test_stats_track_activity(self, bridge):
        bridge.ping()
        bridge.ping()

        assert bridge.stats["commands"] == 2
        assert bridge.stats["connect_attempts"] >= 2

    def test_empty_params_are_omitted_from_the_wire_payload(self, bridge, fake_blender):
        bridge.request("get_scene_info", {})

        assert "params" not in fake_blender.requests[0]

    def test_one_connection_per_request(self, bridge, fake_blender):
        """Each request opens and closes its own socket, so nothing leaks."""
        bridge.ping()
        bridge.ping()
        bridge.ping()

        assert len(fake_blender.requests) == 3


class TestCommandErrors:
    def test_blender_error_becomes_blender_command_error(self, bridge, fake_blender):
        fake_blender.fail_commands["render_scene"] = "no_camera"

        with pytest.raises(BlenderCommandError) as excinfo:
            bridge.request("render_scene")

        assert excinfo.value.code == "no_camera"
        assert "Blender rejected the command" in str(excinfo.value)

    def test_hint_is_included_in_the_message(self, fake_blender):
        fake_blender.scene.objects.clear()
        bridge = BlenderBridge(make_settings(fake_blender.host, fake_blender.port))

        with pytest.raises(BlenderCommandError) as excinfo:
            bridge.request("get_object_info", {"name": "ghost"})

        assert "Hint:" in str(excinfo.value)
        assert excinfo.value.hint is not None

    def test_command_error_is_not_retried(self, fake_blender):
        """Retrying a refused command would duplicate side effects."""
        fake_blender.fail_commands["delete_object"] = "object_not_found"
        bridge = BlenderBridge(
            make_settings(fake_blender.host, fake_blender.port, max_retries=5)
        )

        with pytest.raises(BlenderCommandError):
            bridge.request("delete_object", {"name": "ghost"})

        assert fake_blender.commands_received() == ["delete_object"]


class TestUnavailableBridge:
    def test_connection_refused_raises_unavailable(self):
        bridge = BlenderBridge(
            make_settings("127.0.0.1", free_port(), max_retries=2, backoff_base=0.001)
        )

        with pytest.raises(BlenderUnavailableError) as excinfo:
            bridge.request("ping")

        message = str(excinfo.value)
        assert "Could not connect to the Blender bridge" in message
        # The message must tell the model how to fix it.
        assert "Blender MCP Bridge" in message
        assert "Start MCP Bridge" in message

    def test_retries_before_giving_up(self):
        port = free_port()
        bridge = BlenderBridge(
            make_settings("127.0.0.1", port, max_retries=3, backoff_base=0.001)
        )

        with pytest.raises(BlenderUnavailableError):
            bridge.request("ping")

        assert bridge.stats["connect_attempts"] == 3

    def test_is_available_is_false_and_does_not_raise(self):
        bridge = BlenderBridge(
            make_settings("127.0.0.1", free_port(), max_retries=1, backoff_base=0.001)
        )

        assert bridge.is_available() is False

    def test_backoff_grows_between_attempts(self):
        delays: list[float] = []
        bridge = BlenderBridge(
            make_settings(
                "127.0.0.1",
                free_port(),
                max_retries=3,
                backoff_base=0.5,
                backoff_cap=10.0,
            ),
            sleep=delays.append,
        )

        with pytest.raises(BlenderUnavailableError):
            bridge.request("ping")

        assert delays == [0.5, 1.0]

    def test_backoff_is_capped(self):
        delays: list[float] = []
        bridge = BlenderBridge(
            make_settings(
                "127.0.0.1",
                free_port(),
                max_retries=4,
                backoff_base=1.0,
                backoff_cap=2.0,
            ),
            sleep=delays.append,
        )

        with pytest.raises(BlenderUnavailableError):
            bridge.request("ping")

        assert delays == [1.0, 2.0, 2.0]


class TestTimeouts:
    def test_slow_bridge_raises_a_command_timeout(self):
        """A command timeout must not be retried: Blender already got the request."""
        slow = FakeBlenderBridge(response_delay=1.5).start()
        try:
            bridge = BlenderBridge(
                make_settings(
                    slow.host,
                    slow.port,
                    command_timeout=0.3,
                    max_retries=3,
                    backoff_base=0.001,
                )
            )
            with pytest.raises(BlenderTimeoutError):
                bridge.request("ping")

            # Exactly one attempt: retrying a delivered command is unsafe.
            assert bridge.stats["connect_attempts"] == 1
        finally:
            slow.stop()

    def test_timeout_message_mentions_the_command(self):
        error = BlenderTimeoutError("render_scene", 120.0)

        assert "render_scene" in str(error)
        assert "120" in str(error)
        assert "modal dialog" in str(error)


class TestProtocolErrors:
    def test_non_json_response_raises_protocol_error(self):
        broken = FakeBlenderBridge(malformed=True).start()
        try:
            bridge = BlenderBridge(
                make_settings(broken.host, broken.port, max_retries=1)
            )
            with pytest.raises(BlenderProtocolError) as excinfo:
                bridge.request("ping")

            assert "not valid JSON" in str(excinfo.value)
        finally:
            broken.stop()

    def test_success_without_result_is_a_protocol_error(self):
        bad = FakeBlenderBridge(responder=lambda request: {"status": "success"}).start()
        try:
            bridge = BlenderBridge(make_settings(bad.host, bad.port, max_retries=1))
            with pytest.raises(BlenderProtocolError) as excinfo:
                bridge.request("ping")

            assert "no 'result' object" in str(excinfo.value)
        finally:
            bad.stop()

    def test_unknown_status_is_a_protocol_error(self):
        odd = FakeBlenderBridge(responder=lambda request: {"status": "maybe"}).start()
        try:
            bridge = BlenderBridge(make_settings(odd.host, odd.port, max_retries=1))
            with pytest.raises(BlenderProtocolError) as excinfo:
                bridge.request("ping")

            assert "unrecognised 'status'" in str(excinfo.value)
        finally:
            odd.stop()

    def test_silent_hangup_is_reported(self):
        """A bridge that closes without answering must not look like success."""
        silent = FakeBlenderBridge(responder=lambda request: None).start()
        try:
            bridge = BlenderBridge(
                make_settings(silent.host, silent.port, max_retries=1, command_timeout=1.0)
            )
            with pytest.raises((BlenderTimeoutError, BlenderUnavailableError)):
                bridge.request("ping")
        finally:
            silent.stop()

    def test_empty_result_object_is_accepted(self):
        """An empty result is still a well-formed response."""
        quiet = FakeBlenderBridge(
            responder=lambda request: {"status": "success", "result": {}}
        ).start()
        try:
            bridge = BlenderBridge(make_settings(quiet.host, quiet.port, max_retries=1))
            assert bridge.request("ping") == {}
        finally:
            quiet.stop()

    def test_unknown_command_is_reported_by_the_fake(self, bridge):
        with pytest.raises(BlenderCommandError) as excinfo:
            bridge.request("teleport")

        assert excinfo.value.code == "unknown_command"


class TestThrottling:
    def test_minimum_interval_is_enforced_between_requests(self, fake_blender):
        delays: list[float] = []
        bridge = BlenderBridge(
            make_settings(
                fake_blender.host,
                fake_blender.port,
                min_request_interval=10.0,
            ),
            sleep=delays.append,
        )

        bridge.ping()
        bridge.ping()

        # The first call has nothing to wait for; the second must wait.
        assert len(delays) == 1
        assert 0 < delays[0] <= 10.0

    def test_no_sleep_when_throttling_is_disabled(self, fake_blender):
        delays: list[float] = []
        bridge = BlenderBridge(
            make_settings(fake_blender.host, fake_blender.port, min_request_interval=0.0),
            sleep=delays.append,
        )

        bridge.ping()
        bridge.ping()

        assert delays == []


class TestRequestShape:
    def test_params_are_forwarded_verbatim(self, bridge, fake_blender):
        fake_blender.scene.create("Cube", "cube", [0, 0, 0], 2.0)

        bridge.request("get_object_info", {"name": "Cube"})

        assert fake_blender.requests[0]["params"] == {"name": "Cube"}

    def test_request_is_readable_as_one_line_of_json(self, bridge, fake_blender):
        """The add-on reads one line per request, so the envelope must be single-line."""
        import json

        fake_blender.scene.create("Cube", "cube", [0, 0, 0], 2.0)
        bridge.request("get_object_info", {"name": "Cube", "nested": {"a": [1, 2, 3]}})

        encoded = json.dumps(fake_blender.requests[0])
        assert "\n" not in encoded
        assert fake_blender.requests[0]["params"]["nested"] == {"a": [1, 2, 3]}
