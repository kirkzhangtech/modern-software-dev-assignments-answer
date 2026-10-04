"""Domain-level exceptions.

Service and database code raises these instead of leaking ``sqlite3`` or
Ollama-specific errors. A single exception handler in ``main.py`` translates
them into HTTP responses, so routers stay free of transport concerns.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all expected application errors."""

    status_code: int = 500
    default_message: str = "Internal server error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    status_code = 404
    default_message = "Resource not found"


class ExtractionError(AppError):
    """Raised when the LLM-backed extraction fails.

    This covers an unreachable Ollama server, a model that is not pulled, and
    malformed structured output.
    """

    status_code = 503
    default_message = "Failed to extract action items"


class StorageError(AppError):
    """Raised when a database operation fails."""

    status_code = 500
    default_message = "Database operation failed"