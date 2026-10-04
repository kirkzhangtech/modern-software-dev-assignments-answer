"""Smoke tests for the single-page frontend.

The frontend is plain HTML/JS with no build step, so these tests assert on the
served markup: that each button required by the assignment exists, that it is
wired to the right endpoint, and that untrusted text is escaped before being
injected into the DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_PATH = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


def _frontend() -> str:
    return FRONTEND_PATH.read_text(encoding="utf-8")


def test_frontend_file_exists():
    assert FRONTEND_PATH.is_file()


def test_frontend_exposes_extract_llm_button():
    html = _frontend()

    assert 'id="extract_llm"' in html
    assert ">Extract LLM<" in html


def test_frontend_exposes_list_notes_button():
    html = _frontend()

    assert 'id="list_notes"' in html
    assert ">List Notes<" in html


def test_frontend_keeps_original_extract_button():
    assert 'id="extract"' in _frontend()


def test_extract_llm_button_calls_the_llm_endpoint():
    html = _frontend()

    assert "/action-items/extract-llm" in html
    # The LLM button must target the dedicated endpoint, not the generic one.
    llm_binding = re.search(
        r"\$\('#extract_llm'\)\.addEventListener[\s\S]{0,200}?fetchUrl|"
        r"\$\('#extract_llm'\)[\s\S]{0,300}?'/action-items/extract-llm'",
        html,
    )
    assert llm_binding, "Extract LLM button is not bound to /action-items/extract-llm"


def test_list_notes_button_calls_the_notes_collection_endpoint():
    html = _frontend()

    assert "'/notes'" in html or '"/notes"' in html


def test_frontend_renders_notes_panel():
    html = _frontend()

    assert 'id="notes"' in html
    assert "loadNotes" in html


def test_frontend_escapes_untrusted_text():
    """Extracted items and note bodies must be HTML-escaped before insertion."""
    html = _frontend()

    assert "escapeHtml" in html
    # Items must go through the escaper rather than being interpolated raw.
    assert re.search(r"\$\{escapeHtml\(it\.text\)\}", html)
    assert re.search(r"\$\{escapeHtml\(note\.content\)\}", html)


def test_frontend_surfaces_backend_error_detail():
    html = _frontend()

    assert "readError" in html
    assert "res.status" in html