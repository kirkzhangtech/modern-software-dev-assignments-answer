"""Action item extraction services.

Two extractors are exposed:

* :func:`extract_action_items` - a fast, dependency-free heuristic based on
  bullet markers, keyword prefixes and imperative verbs.
* :func:`extract_action_items_llm` - an LLM-backed extractor that calls a local
  Ollama model and constrains the output to a JSON schema.
"""

from __future__ import annotations

import re

import ollama
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from ..config import settings
from ..errors import ExtractionError

load_dotenv()

BULLET_PREFIX_PATTERN = re.compile(r"^\s*([-*•]|\d+\.)\s+")
KEYWORD_PREFIXES = (
    "todo:",
    "action:",
    "next:",
)

EXTRACTION_SYSTEM_PROMPT = (
    "You extract actionable to-do items from raw notes. "
    "Return only concrete tasks a person can act on. "
    "Ignore narrative, commentary, and work that is already done. "
    "Do not invent items that are absent from the input."
)


class Item(BaseModel):
    """A single action item returned by the model."""

    name: str | None = None


class ItemList(BaseModel):
    """Structured-output envelope matching ``ItemList.model_json_schema()``."""

    items: list[Item]


def _is_action_line(line: str) -> bool:
    stripped = line.strip().lower()
    if not stripped:
        return False
    if BULLET_PREFIX_PATTERN.match(stripped):
        return True
    if any(stripped.startswith(prefix) for prefix in KEYWORD_PREFIXES):
        return True
    if "[ ]" in stripped or "[todo]" in stripped:
        return True
    return False


def extract_action_items_llm(user_prompt: str) -> list[str]:
    """Extract action items from notes using a local Ollama model.

    Args:
        user_prompt: The raw note text to analyse.

    Returns:
        The extracted action items, in model order, with blank names removed.

    Raises:
        ExtractionError: if Ollama is unreachable, the model is unavailable, or
            the response does not match the expected JSON schema.
    """
    try:
        client = ollama.Client(host=settings.ollama_host)
        response = client.chat(
            model=settings.ollama_model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": settings.ollama_temperature},
            format=ItemList.model_json_schema(),
        )
    except Exception as exc:  # noqa: BLE001 - ollama raises several error types
        raise ExtractionError(
            f"Could not reach the Ollama model '{settings.ollama_model}': {exc}"
        ) from exc

    try:
        item_list = ItemList.model_validate_json(response.message.content)
    except ValidationError as exc:
        raise ExtractionError(f"The model returned malformed JSON: {exc}") from exc

    # The schema permits a null name, so filter defensively rather than letting
    # None reach the database layer, where ``text`` is NOT NULL.
    return [item.name.strip() for item in item_list.items if item.name and item.name.strip()]


def extract_action_items(text: str) -> list[str]:
    """Extract action items using deterministic heuristics.

    Recognises bullet markers (``-``, ``*``, ``•``, ``1.``), keyword prefixes
    (``todo:``, ``action:``, ``next:``) and checkbox markers. If nothing matches,
    falls back to splitting into sentences and keeping imperative-sounding ones.

    Returns:
        Deduplicated action items in first-seen order.
    """
    extracted: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not _is_action_line(line):
            continue
        cleaned = BULLET_PREFIX_PATTERN.sub("", line).strip()
        cleaned = cleaned.removeprefix("[ ]").strip()
        cleaned = cleaned.removeprefix("[todo]").strip()
        if cleaned:
            extracted.append(cleaned)

    # Fallback: if nothing matched, split into sentences and keep the
    # imperative-looking ones.
    if not extracted:
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            candidate = sentence.strip()
            if candidate and _looks_imperative(candidate):
                extracted.append(candidate)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for item in extracted:
        lowered = item.lower()
        if lowered not in seen:
            seen.add(lowered)
            unique.append(item)
    return unique


def _looks_imperative(sentence: str) -> bool:
    """Return True if the sentence appears to begin with a command verb."""
    words = re.findall(r"[A-Za-z']+", sentence)
    if not words:
        return False
    imperative_starters = {
        "add",
        "check",
        "create",
        "design",
        "document",
        "fix",
        "implement",
        "investigate",
        "refactor",
        "update",
        "verify",
        "write",
    }
    return words[0].lower() in imperative_starters
