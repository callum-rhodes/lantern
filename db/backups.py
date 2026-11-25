"""Backups.

A backup is a complete copy of the SQLite file made with SQLite's own backup
API, which is safe while the app is running. Restoring copies a backup back
over the live database the same way, after first saving a "pre-restore" copy
of what is there now.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from db.core import PROJECT_DIR, DataError, get_db_path
from db.schema import init_db
from db.settings import get_setting, set_setting

log = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = PROJECT_DIR / "backups"
REQUIRED_TABLES = {"users", "categories", "transactions", "settings"}
_BACKUP_NAME = re.compile(r"^[\w][\w .\-]*\.db$")


def get_backup_dir() -> Path:
    """Folder holding backups (``FINANCE_BACKUP_DIR`` overrides ``backups/`` next to main.py)."""
    path = Path(os.environ.get("FINANCE_BACKUP_DIR", DEFAULT_BACKUP_DIR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_database(source: Path, dest: Path) -> None:
    """Copy one SQLite database over another using SQLite's online backup API."""
    src = dst = None
    try:
        src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        dst = sqlite3.connect(dest, timeout=10)
        src.backup(dst)
    except sqlite3.Error as exc:
        log.exception("Database copy failed")
        raise DataError("Copying the database failed. Check the server log for details.") from exc
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()


def create_backup(prefix: str = "finance") -> str:
    """Save the live database as ``<prefix>-YYYYMMDD-HHMMSS.db``; return the file name."""
    folder = get_backup_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{prefix}-{stamp}.db"
    counter = 1
    while target.exists():  # two backups in the same second
        counter += 1
        target = folder / f"{prefix}-{stamp}-{counter}.db"
    _copy_database(get_db_path(), target)
    return target.name


def resolve_backup(name: str) -> Path:
    """Path of an existing backup file; only plain file names inside the backup folder pass."""
    folder = get_backup_dir().resolve()
    path = (folder / name).resolve()
    if not _BACKUP_NAME.match(name) or path.parent != folder or not path.is_file():
        raise DataError("That backup file no longer exists.")
    return path


def check_backup_file(path: Path) -> dict[str, int]:
    """Make sure ``path`` is a healthy finance database; return its user/transaction counts."""
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise DataError("That file is not a valid database.") from exc
    try:
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not REQUIRED_TABLES <= tables:
                raise DataError("That file is a database, but not a Finance Tracker backup.")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DataError("That backup file is damaged (it failed the integrity check).")
            return {
                "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "transactions": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
            }
        except sqlite3.Error as exc:
            raise DataError("That file is not a valid database.") from exc
    finally:
        conn.close()


def list_backups() -> list[dict[str, Any]]:
    """Backups in the backup folder, newest first."""
    items = []
    for path in get_backup_dir().glob("*.db"):
        if not _BACKUP_NAME.match(path.name):
            continue
        info = path.stat()
        items.append({
            "name": path.name,
            "size": info.st_size,
            "modified": datetime.fromtimestamp(info.st_mtime),
        })
    return sorted(items, key=lambda item: item["modified"], reverse=True)


def import_backup_file(temp_path: Path, original_name: str) -> str:
    """Validate an uploaded database file and keep it in the backup folder; return its name.

    The file is stored as ``uploaded-<original name>`` (numbered if that exists).
    The temporary file is removed either way.
    """
    try:
        check_backup_file(temp_path)
        stem = re.sub(r"[^\w .\-]", "_", Path(original_name).stem).strip(" .") or "backup"
        folder = get_backup_dir()
        target = folder / f"uploaded-{stem}.db"
        counter = 1
        while target.exists():
            counter += 1
            target = folder / f"uploaded-{stem}-{counter}.db"
        temp_path.replace(target)
        return target.name
    finally:
        temp_path.unlink(missing_ok=True)


def restore_backup(name: str) -> str:
    """Replace the live database with a backup; return the name of the safety copy.

    The current data is first saved as ``pre-restore-<timestamp>.db``, so a restore
    can itself be undone. The browser-signing secret is kept, so open browsers
    stay signed in to their chosen user/currency, and the schema is brought up to
    date in case the backup came from an older version of the app.
    """
    path = resolve_backup(name)
    check_backup_file(path)
    safety = create_backup("pre-restore")
    secret = get_setting("storage_secret")
    _copy_database(path, get_db_path())
    init_db()
    if secret:
        set_setting("storage_secret", secret)
    return safety


def delete_backup(name: str) -> None:
    """Delete one backup file."""
    resolve_backup(name).unlink()
