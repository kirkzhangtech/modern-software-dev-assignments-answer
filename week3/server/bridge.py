"""Client for the Blender bridge socket protocol.

The protocol is one JSON object per line, one request per connection:

    request   {"type": "<command>", "params": {...}}\\n
    response  {"status": "success", "result": {...}}\\n
    response  {"status": "error", "error": {"code": ..., "message": ...}}\\n

:class:`BlenderBridge` adds the resilience the MCP layer needs: connection
retries with exponential backoff, distinct timeouts for connect and command, a
minimum interval between commands, and translation of every failure mode into a
typed exception with an actionable message.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any

from .config import Settings
from .config import settings as default_settings
from .errors import (
    BlenderCommandError,
    BlenderMCPError,
    BlenderProtocolError,
    BlenderTimeoutError,
    BlenderUnavailableError,
)

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 32 * 1024 * 1024

# Failures that mean "Blender is not listening yet" rather than "the request was
# wrong". These are worth retrying.
#
# TimeoutError is in the list because connecting to a closed port raises
# ConnectionRefusedError on Linux/macOS but TimeoutError on Windows, and
# ``socket.timeout`` is an alias of the built-in TimeoutError from Python 3.10.
RETRYABLE_CONNECT_ERRORS = (
    ConnectionRefusedError,
    ConnectionResetError,
    ConnectionAbortedError,
    FileNotFoundError,
    TimeoutError,
)


class BlenderBridge:
    """Sends commands to the Blender add-on and returns their results."""

    def __init__(self, settings: Settings | None = None, sleep=time.sleep) -> None:
        self.settings = settings or default_settings
        self._sleep = sleep
        self._min_interval_lock = threading.Lock()
        self._last_request_at = 0.0
        self._connect_attempts = 0
        self._command_count = 0

    # -- diagnostics -------------------------------------------------------- #

    @property
    def stats(self) -> dict[str, int]:
        """Counters useful for diagnosing flaky bridges."""
        return {"connect_attempts": self._connect_attempts, "commands": self._command_count}

    # -- public API --------------------------------------------------------- #

    def request(self, command: str, params: dict[str, Any] | None = None) -> dict:
        """Send ``command`` with ``params`` and return the ``result`` object.

        Raises:
            BlenderUnavailableError: Blender is not reachable.
            BlenderTimeoutError: Blender did not answer in time.
            BlenderCommandError: Blender refused the command.
            BlenderProtocolError: The response was not intelligible.
        """
        self._throttle()

        payload: dict[str, Any] = {"type": command}
        if params:
            payload["params"] = params

        response = self._send_with_retries(payload, command)
        self._command_count += 1
        return response

    def ping(self) -> dict:
        """Check that the bridge is alive and list the commands it supports."""
        return self.request("ping")

    def is_available(self) -> bool:
        """Return True if the bridge answers a ping, without raising."""
        try:
            self.ping()
        except BlenderMCPError:
            return False
        return True

    # -- internals ---------------------------------------------------------- #

    def _throttle(self) -> None:
        """Enforce a minimum gap between commands.

        Blender executes every command on its main thread, so a burst of
        requests makes the UI unresponsive. Serialising them with a small delay
        keeps Blender usable without meaningfully slowing down an agent.
        """
        interval = self.settings.min_request_interval
        if interval <= 0:
            return
        with self._min_interval_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < interval:
                self._sleep(interval - elapsed)
            self._last_request_at = time.monotonic()

    def _send_with_retries(self, payload: dict[str, Any], command: str) -> dict:
        """Send one request, retrying only connection failures.

        A command that Blender explicitly rejected is *not* retried: the same
        input would fail the same way, and retrying would duplicate side effects.
        """
        attempts = max(1, self.settings.max_retries)
        last_error: Exception | None = None

        for attempt in range(attempts):
            self._connect_attempts += 1
            try:
                return self._send_once(payload, command)
            except RETRYABLE_CONNECT_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "Bridge connection attempt %d/%d failed: %s: %s",
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                    exc,
                )

            if attempt < attempts - 1:
                delay = min(
                    self.settings.backoff_base * (2**attempt), self.settings.backoff_cap
                )
                logger.info("Retrying in %.2fs", delay)
                self._sleep(delay)

        detail = f"last error: {type(last_error).__name__}: {last_error}"
        raise BlenderUnavailableError(self.settings.host, self.settings.port, detail)

    def _send_once(self, payload: dict[str, Any], command: str) -> dict:
        """Open a connection, exchange one request/response pair, and close."""
        encoded = json.dumps(payload).encode("utf-8") + b"\n"

        # The connect phase is separated so that its failures stay retryable.
        # A failure *after* connecting means Blender received the command, and
        # retrying that could duplicate a side effect such as object creation.
        try:
            connection = socket.create_connection(
                (self.settings.host, self.settings.port),
                timeout=self.settings.connect_timeout,
            )
        except RETRYABLE_CONNECT_ERRORS:
            raise
        except OSError as exc:
            raise BlenderUnavailableError(
                self.settings.host, self.settings.port, f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            connection.settimeout(self.settings.command_timeout)
            connection.sendall(encoded)
            raw = self._read_line(connection, command)
        finally:
            connection.close()

        return self._parse_response(raw, command)

    def _read_line(self, connection: socket.socket, command: str) -> bytes:
        """Read until the newline that terminates the response."""
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = connection.recv(65536)
            except socket.timeout as exc:
                raise BlenderTimeoutError(command, self.settings.command_timeout) from exc
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise BlenderProtocolError(
                    f"The bridge response exceeded {MAX_RESPONSE_BYTES} bytes and was truncated."
                )
            if b"\n" in chunk:
                break

        raw = b"".join(chunks)
        if not raw.strip():
            raise BlenderTimeoutError(command, self.settings.command_timeout)
        return raw

    def _parse_response(self, raw: bytes, command: str) -> dict:
        """Validate the envelope and unwrap either the result or the error."""
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            preview = raw[:200].decode("utf-8", errors="replace")
            raise BlenderProtocolError(
                f"Blender sent a response that is not valid JSON ({exc}). "
                f"First 200 bytes: {preview!r}"
            ) from exc

        if not isinstance(decoded, dict):
            raise BlenderProtocolError(
                f"Expected a JSON object from Blender, got {type(decoded).__name__}."
            )

        status = decoded.get("status")
        if status == "success":
            result = decoded.get("result")
            if not isinstance(result, dict):
                raise BlenderProtocolError(
                    f"Successful response to '{command}' had no 'result' object."
                )
            return result

        if status == "error":
            error = decoded.get("error") or {}
            raise BlenderCommandError(
                code=str(error.get("code", "unknown_error")),
                message=str(error.get("message", "Blender reported an unspecified error.")),
                hint=error.get("hint"),
            )

        raise BlenderProtocolError(
            f"Response to '{command}' had an unrecognised 'status': {status!r}."
        )
