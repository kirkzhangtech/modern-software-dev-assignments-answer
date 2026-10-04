from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..schemas import (
    ActionItemResponse,
    ErrorResponse,
    ExtractedItem,
    ExtractRequest,
    ExtractResponse,
    MarkDoneRequest,
    MarkDoneResponse,
)
from ..services.extract import extract_action_items, extract_action_items_llm

router = APIRouter(
    prefix="/action-items",
    tags=["action-items"],
    responses={404: {"model": ErrorResponse}},
)

EXTRACTORS = {
    "heuristic": extract_action_items,
    "llm": extract_action_items_llm,
}


@router.post("/extract", response_model=ExtractResponse)
def extract(payload: ExtractRequest) -> ExtractResponse:
    """Extract action items from notes using the requested method.

    The ``method`` field selects the extractor: ``heuristic`` (regex rules) or
    ``llm`` (local Ollama model). When ``save_note`` is set, the source text is
    persisted first and the extracted items are linked to it.
    """
    return _run_extraction(payload.text, payload.save_note, payload.method)


def _run_extraction(text: str, save_note: bool, method: str) -> ExtractResponse:
    """Shared extraction pipeline used by both extraction endpoints.

    Persists the source note (when requested), runs the selected extractor, and
    stores the resulting action items linked to that note.

    Raises:
        ExtractionError: mapped to HTTP 503 when the LLM path fails.
    """
    note_id = db.insert_note(text) if save_note else None

    extractor = EXTRACTORS[method]
    texts = extractor(text)
    ids = db.insert_action_items(texts, note_id=note_id)

    return ExtractResponse(
        method=method,
        note_id=note_id,
        items=[
            ExtractedItem(id=item_id, text=text_item)
            for item_id, text_item in zip(ids, texts)
            if text_item and text_item.strip()
        ],
    )


@router.post("/extract-llm", response_model=ExtractResponse)
def extract_with_llm(payload: ExtractRequest) -> ExtractResponse:
    """Extract action items using the local Ollama model.

    This is the dedicated LLM endpoint backing the frontend's "Extract LLM"
    button. It behaves like ``/extract`` with ``method="llm"``, but is exposed as
    its own route so the LLM path can be discovered in the OpenAPI docs and
    triggered independently of the heuristic path.

    Raises:
        ExtractionError: mapped to HTTP 503 by the handler in ``main.py`` when
            Ollama is unreachable or returns malformed output.
    """
    return _run_extraction(payload.text, payload.save_note, "llm")


@router.get("", response_model=list[ActionItemResponse])
def list_all(note_id: int | None = None) -> list[ActionItemResponse]:
    """Return action items, optionally filtered by note id."""
    return [
        ActionItemResponse(
            id=row["id"],
            note_id=row["note_id"],
            text=row["text"],
            done=bool(row["done"]),
            created_at=row["created_at"],
        )
        for row in db.list_action_items(note_id=note_id)
    ]


@router.post("/{action_item_id}/done", response_model=MarkDoneResponse)
def mark_done(action_item_id: int, payload: MarkDoneRequest) -> MarkDoneResponse:
    """Toggle the completion state of an action item.

    Raises:
        NotFoundError: mapped to HTTP 404 when the id does not exist.
    """
    db.set_action_item_done(action_item_id, payload.done)
    return MarkDoneResponse(id=action_item_id, done=payload.done)


