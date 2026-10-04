from __future__ import annotations

from fastapi import APIRouter, status

from .. import db
from ..schemas import ErrorResponse, NoteCreate, NoteResponse

router = APIRouter(prefix="/notes", tags=["notes"], responses={404: {"model": ErrorResponse}})


@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
def create_note(payload: NoteCreate) -> NoteResponse:
    """Create a note from raw text."""
    note_id = db.insert_note(payload.content)
    note = db.get_note(note_id)
    return NoteResponse(
        id=note["id"],
        content=note["content"],
        created_at=note["created_at"],
    )


@router.get("", response_model=list[NoteResponse])
def list_all_notes() -> list[NoteResponse]:
    """Return every note, newest first."""
    return [
        NoteResponse(
            id=row["id"],
            content=row["content"],
            created_at=row["created_at"],
        )
        for row in db.list_notes()
    ]


@router.get("/{note_id}", response_model=NoteResponse)
def get_single_note(note_id: int) -> NoteResponse:
    """Return a single note by id.

    Raises:
        NotFoundError: mapped to HTTP 404 by the handler in ``main.py``.
    """
    row = db.get_note(note_id)
    return NoteResponse(
        id=row["id"],
        content=row["content"],
        created_at=row["created_at"],
    )


