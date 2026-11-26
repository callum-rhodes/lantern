"""Connection handling and shared helpers for the database package.

Conventions
-----------
* Amounts are signed integer minor units (pence/cents) in the base currency:
  income is positive, spending is negative.
* Every function that touches user data takes ``user_id`` and scopes its
  queries to it, so users can never see each other's data.
* Problems the user can fix (bad input, category still in use, ...) raise
  ``DataError`` with a friendly message that pages show in a notification.
  Unexpected SQLite failures are logged and re-raised as ``DataError`` too.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# This file lives in db/, so the default database sits one level up, next to main.py.
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "lantern.db"


class DataError(Exception):
    """A problem with the data that can be explained to the user."""


def get_db_path() -> Path:
    """Return the database file path (``LANTERN_DB_PATH`` overrides the default)."""
    return Path(os.environ.get("LANTERN_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open a connection; commit on success, roll back on error, always close."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        log.warning("Integrity error: %s", exc)
        raise DataError(
            "That change conflicts with existing data (a duplicate, or something still in use)."
        ) from exc
    except sqlite3.Error as exc:
        conn.rollback()
        log.exception("Database error")
        raise DataError("A database error occurred. Check the server log for details.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def all_rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """Convert every row of a cursor to a plain dict."""
    return [dict(row) for row in cursor.fetchall()]


def one_row(cursor: sqlite3.Cursor) -> dict[str, Any] | None:
    """Convert the next row of a cursor to a dict (or None)."""
    row = cursor.fetchone()
    return dict(row) if row else None
