"""Shared pytest setup: every test gets its own throw-away database.

``LANTERN_DB_PATH`` is pointed at a temporary file before each test, so your real
``lantern.db`` is never touched and no test can see another test's data.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from db import schema, users

# The shared_ui tests import NiceGUI; keep it from writing its storage into the project folder.
os.environ.setdefault("NICEGUI_STORAGE_PATH", tempfile.mkdtemp(prefix="lantern-nicegui-"))


@pytest.fixture(autouse=True)
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh, empty database (schema created) for every test."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("LANTERN_DB_PATH", str(path))
    monkeypatch.setenv("LANTERN_BACKUP_DIR", str(tmp_path / "backups"))
    schema.init_db()
    return path


@pytest.fixture
def abc() -> tuple[int, int, int]:
    """Three brand-new users (Alice, Bob, Cara), each with the default categories."""
    alice, bob, cara = (users.add_user(name, "pass1234") for name in ("Alice", "Bob", "Cara"))
    return alice, bob, cara
