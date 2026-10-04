"""Application configuration.

Every tunable value used by the backend lives here, so that nothing needs to be
hard-coded inside routers or services. Values are read from environment
variables (optionally loaded from a ``.env`` file) and fall back to sensible
defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# week2/app/config.py -> week2/
BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"


def _env_str(name: str, default: str) -> str:
    """Read a string environment variable, falling back to ``default``."""
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, ignoring malformed values."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float environment variable, ignoring malformed values."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class Settings:
    """Runtime settings for the Action Item Extractor backend."""

    def __init__(self) -> None:
        load_dotenv()

        # --- Storage -----------------------------------------------------
        self.db_path: Path = Path(_env_str("APP_DB_PATH", str(DATA_DIR / "app.db")))

        # --- LLM extraction (Ollama) -------------------------------------
        self.ollama_model: str = _env_str("OLLAMA_MODEL", "mistral-nemo:12b")
        self.ollama_temperature: float = _env_float("OLLAMA_TEMPERATURE", 0.5)
        self.ollama_host: str = _env_str("OLLAMA_HOST", "http://127.0.0.1:11434")

        # --- Application metadata ----------------------------------------
        self.app_title: str = _env_str("APP_TITLE", "Action Item Extractor")
        self.app_version: str = _env_str("APP_VERSION", "0.2.0")


settings = Settings()