"""Pydantic models defining the public API contract.

Every endpoint declares its request and response models here. This gives us:

* automatic validation of incoming payloads (422 on bad input),
* an accurate OpenAPI schema at ``/docs``,
* response filtering, so internal fields never leak to clients.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NoteCreate(BaseModel):
    """Request body for creating a note."""

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(..., min_length=1, description="Raw note text.")

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class NoteResponse(BaseModel):
    """A stored note."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    created_at: str


class ExtractRequest(BaseModel):
    """Request body for action item extraction."""

    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(..., min_length=1, description="Free-form notes to analyse.")
    save_note: bool = Field(
        default=True,
        description="Persist the source text as a note before extracting.",
    )
    method: Literal["heuristic", "llm"] = Field(
        default="heuristic",
        description=(
            "Which extractor to use. 'heuristic' is the regex-based rule set; "
            "'llm' calls the local Ollama model."
        ),
    )

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ExtractedItem(BaseModel):
    """A single extracted action item, as returned by extraction."""

    id: int | None = Field(
        default=None,
        description="Database id; null for items that were not persisted.",
    )
    text: str


class ExtractResponse(BaseModel):
    """Result of an extraction request."""

    method: Literal["heuristic", "llm"]
    note_id: int | None = None
    items: list[ExtractedItem]


class ActionItemResponse(BaseModel):
    """A stored action item."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    note_id: int | None = None
    text: str
    done: bool
    created_at: str


class MarkDoneRequest(BaseModel):
    """Request body for toggling an action item's completion state."""

    done: bool = True


class MarkDoneResponse(BaseModel):
    """Result of toggling an action item."""

    id: int
    done: bool


class ErrorResponse(BaseModel):
    """Uniform error payload returned by every failing endpoint."""

    detail: str