"""Unit tests for the action item extraction services.

``extract_action_items_llm`` is backed by a local Ollama model, so every test
stubs ``week2.app.services.extract.ollama.chat``. The suite therefore runs
offline and never requires a model to be pulled, while still verifying:

* the prompt / structured-output contract sent to the model,
* how the JSON response is parsed back into a list of strings,
* how transport and schema failures are surfaced as ``ExtractionError``.
"""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from ..app.errors import ExtractionError
from ..app.services import extract as extract_module
from ..app.services.extract import (
    ItemList,
    extract_action_items,
    extract_action_items_llm,
)


def _llm_response(content: str) -> SimpleNamespace:
    """Mimic the shape of the object returned by ``ollama.chat``."""
    return SimpleNamespace(message=SimpleNamespace(content=content))


def _items_payload(names: list[Any]) -> str:
    """Build the JSON body the model is asked to produce."""
    return json.dumps({"items": [{"name": name} for name in names]})


@pytest.fixture
def stub_chat(monkeypatch):
    """Replace the Ollama client with a deterministic stub.

    Returns a callable that installs the stub with a canned JSON payload and
    returns the recorded calls, so tests can assert on the request sent to the
    model. The stub also records the ``host`` the client was constructed with.
    """

    def _install(json_payload: str) -> list[dict[str, Any]]:
        recorded_calls: list[dict[str, Any]] = []

        class FakeClient:
            def __init__(self, host: str | None = None, **kwargs: Any) -> None:
                recorded_calls.append({"host": host, "kwargs": kwargs})

            def chat(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
                recorded_calls.append({"args": args, "kwargs": kwargs})
                return _llm_response(json_payload)

        monkeypatch.setattr(extract_module.ollama, "Client", FakeClient)
        return recorded_calls

    return _install


# ---------------------------------------------------------------------------
# extract_action_items_llm
# ---------------------------------------------------------------------------


def test_extract_action_items_llm_returns_parsed_names(stub_chat):
    stub_chat(_items_payload(["Set up database", "Write tests"]))

    assert extract_action_items_llm("some notes") == ["Set up database", "Write tests"]


def test_extract_action_items_llm_sends_prompt_and_schema(stub_chat):
    calls = stub_chat(_items_payload(["Do the thing"]))
    prompt = "todo: ship the release"

    extract_action_items_llm(prompt)

    # First recorded call is the Client(host=...) construction.
    assert calls[0]["host"] == extract_module.settings.ollama_host

    assert len(calls) == 2
    kwargs = calls[1]["kwargs"]
    assert kwargs["messages"][-1] == {"role": "user", "content": prompt}
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["format"] == ItemList.model_json_schema()
    assert kwargs["model"] == extract_module.settings.ollama_model
    assert kwargs["options"] == {"temperature": extract_module.settings.ollama_temperature}


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        # Bullet / checkbox lists.
        ("- [ ] Set up database\n- Write tests", ["Set up database", "Write tests"]),
        # Keyword-prefixed lines.
        (
            "TODO: fix the login bug\nACTION: update the docs",
            ["fix the login bug", "update the docs"],
        ),
        # Free-form prose.
        ("Reminder: call the dentist next Tuesday.", ["call the dentist next Tuesday"]),
        # Empty input yields no items.
        ("", []),
    ],
)
def test_extract_action_items_llm_handles_various_inputs(stub_chat, raw_text, expected):
    stub_chat(_items_payload(expected))

    assert extract_action_items_llm(raw_text) == expected


def test_extract_action_items_llm_tolerates_json_whitespace(stub_chat):
    stub_chat('\n  {"items": [{"name": "Trim me"}]}  \n')

    assert extract_action_items_llm("notes") == ["Trim me"]


def test_extract_action_items_llm_drops_null_and_blank_names(stub_chat):
    # The schema permits a null name; those must never reach the DB layer,
    # where ``text`` is NOT NULL.
    stub_chat(_items_payload(["Keep me", None, "   ", "  Trim me  "]))

    assert extract_action_items_llm("notes") == ["Keep me", "Trim me"]


def test_extract_action_items_llm_raises_on_malformed_response(stub_chat):
    stub_chat("this is not JSON")

    with pytest.raises(ExtractionError):
        extract_action_items_llm("notes")


def test_extract_action_items_llm_wraps_transport_failure(monkeypatch):
    class BoomClient:
        def __init__(self, host: str | None = None, **kwargs: Any) -> None:
            pass

        def chat(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("connection refused")

    monkeypatch.setattr(extract_module.ollama, "Client", BoomClient)

    with pytest.raises(ExtractionError, match="Could not reach the Ollama model"):
        extract_action_items_llm("notes")


# ---------------------------------------------------------------------------
# extract_action_items (heuristic extractor)
# ---------------------------------------------------------------------------


def test_extract_bullets_and_checkboxes():
    text = """
    Notes from meeting:
    - [ ] Set up database
    * implement API extract endpoint
    1. Write tests
    Some narrative sentence.
    """.strip()

    items = extract_action_items(text)
    assert "Set up database" in items
    assert "implement API extract endpoint" in items
    assert "Write tests" in items
