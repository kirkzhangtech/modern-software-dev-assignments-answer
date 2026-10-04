"""SQLite data-access layer.

Design notes
------------
* Every connection is **explicitly closed**. ``sqlite3.Connection.__exit__``
  only commits or rolls back a transaction; it does not close the handle. The
  previous ``with get_connection() as conn:`` pattern therefore leaked one
  connection per request.
* Connections are handed out through a small context manager
  (:func:`db_session`) that guarantees closure.
* Foreign keys are enabled explicitly - SQLite disables them per-connection by
  default, so ``action_items.note_id`` was never actually enforced.
* Mutations report whether a row was affected, so callers can detect a no-op
  instead of silently reporting success.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import settings
from .errors import NotFoundError, StorageError

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS action_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER,
        text TEXT NOT NULL,
        done INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_action_items_note_id ON action_items(note_id);",
)


def _connect() -> sqlite3.Connection:
    """Open a configured connection to the SQLite file."""
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    """Yield a connection and guarantee it is closed afterwards."""
    connection = _connect()
    try:
        yield connection
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise StorageError(f"Database operation failed: {exc}") from exc
    finally:
        connection.close()


def init_db() -> None:
    """Create tables and indexes if they do not already exist."""
    with db_session() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


def insert_note(content: str) -> int:
    """Insert a note and return its new id."""
    with db_session() as connection:
        cursor = connection.execute("INSERT INTO notes (content) VALUES (?)", (content,))
        return int(cursor.lastrowid)


def list_notes() -> list[sqlite3.Row]:
    """Return all notes, newest first."""
    with db_session() as connection:
        cursor = connection.execute(
            "SELECT id, content, created_at FROM notes ORDER BY id DESC"
        )
        return list(cursor.fetchall())


def get_note(note_id: int) -> sqlite3.Row:
    """Return a single note.

    Raises:
        NotFoundError: if the note does not exist.
    """
    with db_session() as connection:
        cursor = connection.execute(
            "SELECT id, content, created_at FROM notes WHERE id = ?",
            (note_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise NotFoundError(f"Note {note_id} not found")
    return row


def insert_action_items(items: list[str], note_id: int | None = None) -> list[int]:
    """Insert action items in one transaction and return their ids.

    Blank entries are skipped rather than inserted, because ``text`` is NOT NULL
    and an empty action item carries no meaning.
    """
    cleaned = [item for item in items if item and item.strip()]
    if not cleaned:
        return []

    with db_session() as connection:
        ids: list[int] = []
        for item in cleaned:
            cursor = connection.execute(
                "INSERT INTO action_items (note_id, text) VALUES (?, ?)",
                (note_id, item),
            )
            ids.append(int(cursor.lastrowid))
        return ids


def list_action_items(note_id: int | None = None) -> list[sqlite3.Row]:
    """Return action items, optionally filtered by note."""
    query = "SELECT id, note_id, text, done, created_at FROM action_items"
    params: tuple = ()
    if note_id is not None:
        query += " WHERE note_id = ?"
        params = (note_id,)
    query += " ORDER BY id DESC"

    with db_session() as connection:
        cursor = connection.execute(query, params)
        return list(cursor.fetchall())


def set_action_item_done(action_item_id: int, done: bool) -> None:
    """Mark an action item done or undone.

    Raises:
        NotFoundError: if no row with that id exists. The previous version
            silently reported success for unknown ids.
    """
    with db_session() as connection:
        cursor = connection.execute(
            "UPDATE action_items SET done = ? WHERE id = ?",
            (1 if done else 0, action_item_id),
        )
        affected = cursor.rowcount
    if affected == 0:
        raise NotFoundError(f"Action item {action_item_id} not found")


# Backwards-compatible alias for the pre-refactor router name.
mark_action_item_done = set_action_item_done


