"""Integration tests for the API layer.

These exercise the real routing, validation and error-translation wiring using
FastAPI's TestClient against a temporary SQLite file. No Ollama model is
required: the ``llm`` method is stubbed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """Build a TestClient backed by an isolated, temporary database."""
    from ..app import config, db, main

    monkeypatch.setattr(config.settings, "db_path", tmp_path / "test.db")
    db.init_db()

    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_health_of_root_serves_frontend(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Action Item Extractor" in response.text


def test_openapi_schema_documents_all_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert "/notes" in paths
    assert "/notes/{note_id}" in paths
    assert "/action-items/extract" in paths
    assert "/action-items/extract-llm" in paths
    assert "/action-items/{action_item_id}/done" in paths


def test_create_and_fetch_note(client):
    created = client.post("/notes", json={"content": "  buy milk  "})

    assert created.status_code == 201
    body = created.json()
    assert body["content"] == "buy milk"

    fetched = client.get(f"/notes/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["content"] == "buy milk"


def test_list_notes_returns_newest_first(client):
    first = client.post("/notes", json={"content": "first"}).json()["id"]
    second = client.post("/notes", json={"content": "second"}).json()["id"]

    ids = [note["id"] for note in client.get("/notes").json()]

    assert ids == [second, first]


@pytest.mark.parametrize(
    "payload",
    [
        {},  # missing field
        {"content": ""},  # empty
        {"content": "   "},  # blank
        {"content": 42},  # wrong type
    ],
)
def test_create_note_rejects_invalid_payload(client, payload):
    response = client.post("/notes", json=payload)

    assert response.status_code == 422
    assert "detail" in response.json()


def test_get_unknown_note_returns_404(client):
    response = client.get("/notes/9999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Note 9999 not found"}


def test_extract_heuristic_persists_note_and_items(client):
    response = client.post(
        "/action-items/extract",
        json={"text": "- [ ] Set up database\n- Write tests", "method": "heuristic"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "heuristic"
    assert body["note_id"] is not None
    assert [item["text"] for item in body["items"]] == ["Set up database", "Write tests"]
    assert all(item["id"] is not None for item in body["items"])


def test_extract_without_save_note_leaves_note_id_null(client):
    response = client.post(
        "/action-items/extract",
        json={"text": "- Write tests", "save_note": False},
    )

    assert response.status_code == 200
    assert response.json()["note_id"] is None
    assert client.get("/notes").json() == []


def test_extract_rejects_blank_text(client):
    response = client.post("/action-items/extract", json={"text": "   "})

    assert response.status_code == 422


def test_extract_rejects_unknown_method(client):
    response = client.post("/action-items/extract", json={"text": "hi", "method": "magic"})

    assert response.status_code == 422


def test_extract_llm_maps_failure_to_503(client, monkeypatch):
    from ..app.errors import ExtractionError
    from ..app.routers import action_items as action_items_router

    def boom(_text: str):
        raise ExtractionError("model unavailable")

    monkeypatch.setitem(action_items_router.EXTRACTORS, "llm", boom)

    response = client.post("/action-items/extract", json={"text": "hi", "method": "llm"})

    assert response.status_code == 503
    assert response.json() == {"detail": "model unavailable"}


def test_extract_llm_endpoint_uses_llm_extractor(client, monkeypatch):
    from ..app.routers import action_items as action_items_router

    seen: list[str] = []

    def fake_llm(text: str) -> list[str]:
        seen.append(text)
        return ["model task A", "model task B"]

    monkeypatch.setitem(action_items_router.EXTRACTORS, "llm", fake_llm)

    response = client.post("/action-items/extract-llm", json={"text": "raw meeting notes"})

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "llm"
    assert seen == ["raw meeting notes"]
    assert [item["text"] for item in body["items"]] == ["model task A", "model task B"]


def test_extract_llm_endpoint_persists_note_and_items(client, monkeypatch):
    from ..app.routers import action_items as action_items_router

    monkeypatch.setitem(
        action_items_router.EXTRACTORS, "llm", lambda _text: ["persisted task"]
    )

    body = client.post("/action-items/extract-llm", json={"text": "save me"}).json()

    assert body["note_id"] is not None
    notes = client.get("/notes").json()
    assert [n["content"] for n in notes] == ["save me"]

    items = client.get("/action-items", params={"note_id": body["note_id"]}).json()
    assert [i["text"] for i in items] == ["persisted task"]


def test_extract_llm_endpoint_respects_save_note_false(client, monkeypatch):
    from ..app.routers import action_items as action_items_router

    monkeypatch.setitem(action_items_router.EXTRACTORS, "llm", lambda _text: ["a task"])

    body = client.post(
        "/action-items/extract-llm", json={"text": "do not save", "save_note": False}
    ).json()

    assert body["note_id"] is None
    assert client.get("/notes").json() == []


def test_extract_llm_endpoint_rejects_blank_text(client):
    assert client.post("/action-items/extract-llm", json={"text": "  "}).status_code == 422


def test_extract_llm_endpoint_maps_failure_to_503(client, monkeypatch):
    from ..app.errors import ExtractionError
    from ..app.routers import action_items as action_items_router

    def boom(_text: str):
        raise ExtractionError("ollama unreachable")

    monkeypatch.setitem(action_items_router.EXTRACTORS, "llm", boom)

    response = client.post("/action-items/extract-llm", json={"text": "hi"})

    assert response.status_code == 503
    assert response.json() == {"detail": "ollama unreachable"}


def test_extract_llm_endpoint_is_distinct_from_heuristic(client, monkeypatch):
    """The LLM route must never fall through to the regex extractor."""
    from ..app.routers import action_items as action_items_router

    monkeypatch.setitem(
        action_items_router.EXTRACTORS, "llm", lambda _text: ["from the model"]
    )

    llm_body = client.post("/action-items/extract-llm", json={"text": "- a task"}).json()
    heuristic_body = client.post(
        "/action-items/extract", json={"text": "- a task"}
    ).json()

    assert [i["text"] for i in llm_body["items"]] == ["from the model"]
    assert [i["text"] for i in heuristic_body["items"]] == ["a task"]


def test_list_action_items_filters_by_note(client):
    extracted = client.post(
        "/action-items/extract", json={"text": "- first task\n- second task"}
    ).json()
    note_id = extracted["note_id"]

    everything = client.get("/action-items").json()
    filtered = client.get("/action-items", params={"note_id": note_id}).json()

    assert len(everything) == 2
    assert len(filtered) == 2
    assert all(item["note_id"] == note_id for item in filtered)


def test_mark_done_toggles_state(client):
    item_id = client.post("/action-items/extract", json={"text": "- a task"}).json()["items"][0][
        "id"
    ]

    marked = client.post(f"/action-items/{item_id}/done", json={"done": True})
    assert marked.status_code == 200
    assert marked.json() == {"id": item_id, "done": True}
    assert client.get("/action-items").json()[0]["done"] is True

    unmarked = client.post(f"/action-items/{item_id}/done", json={"done": False})
    assert unmarked.json() == {"id": item_id, "done": False}
    assert client.get("/action-items").json()[0]["done"] is False


def test_mark_unknown_action_item_returns_404(client):
    # Regression test: the pre-refactor version silently returned 200.
    response = client.post("/action-items/424242/done", json={"done": True})

    assert response.status_code == 404
    assert response.json() == {"detail": "Action item 424242 not found"}


def test_foreign_key_is_enforced(client):
    """A dangling note_id must be rejected now that PRAGMA foreign_keys is on."""
    from ..app import db
    from ..app.errors import StorageError

    with pytest.raises(StorageError):
        db.insert_action_items(["orphan"], note_id=99999)


def test_connections_are_not_leaked(client):
    """Repeated requests must not accumulate open SQLite connections."""
    import gc
    import sqlite3

    for _ in range(25):
        client.get("/notes")

    gc.collect()
    live = [obj for obj in gc.get_objects() if isinstance(obj, sqlite3.Connection)]
    assert len(live) == 0