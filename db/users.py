"""Users and passwords."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from typing import Any

from db import categories, shared_transactions
from db.core import DataError, all_rows, connect, one_row

MIN_PASSWORD_LENGTH = 4
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_USER_COLS = "id, name, password_hash IS NOT NULL AS has_password"


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Salted scrypt hash, stored as 'scrypt$<salt>$<digest>' (hex)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _check_hash(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except ValueError:
        return False
    return scheme == "scrypt" and hmac.compare_digest(digest.hex(), digest_hex)


def _validate_password(password: str | None) -> str:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise DataError(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters.")
    return password  # type: ignore[return-value]


def set_password(user_id: int, password: str) -> None:
    """Set or replace a user's password (also used by the CLI reset)."""
    hashed = hash_password(_validate_password(password))
    with connect() as conn:
        cur = conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user_id))
        if cur.rowcount == 0:
            raise DataError("User not found.")


def verify_password(user_id: int, password: str) -> bool:
    """True if the password matches. False for wrong passwords and users without one."""
    with connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row["password_hash"] and _check_hash(password or "", row["password_hash"]))


def session_token(user_id: int) -> str | None:
    """Changes whenever the password changes, so old logins stop working."""
    with connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row["password_hash"]:
        return None
    return hashlib.sha256(row["password_hash"].encode()).hexdigest()


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

def list_users() -> list[dict[str, Any]]:
    """All users, ordered by name (never includes the hash)."""
    with connect() as conn:
        return all_rows(conn.execute(f"SELECT {_USER_COLS} FROM users ORDER BY name COLLATE NOCASE"))


def get_user(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        return one_row(conn.execute(f"SELECT {_USER_COLS} FROM users WHERE id = ?", (user_id,)))


def _next_user_id(conn: sqlite3.Connection) -> int:
    """Next free user id. Never reuses the id of a deleted user who still appears in shared
    records, which would hand the newcomer someone else's history."""
    row = conn.execute(
        "SELECT MAX(m) FROM ("
        " SELECT MAX(id) AS m FROM users"
        " UNION ALL SELECT MAX(user_id) FROM shared_participants"
        " UNION ALL SELECT MAX(creator_id) FROM shared_transactions"
        " UNION ALL SELECT MAX(rejected_by) FROM shared_transactions"
        " UNION ALL SELECT MAX(from_user_id) FROM settlements"
        " UNION ALL SELECT MAX(to_user_id) FROM settlements"
        " UNION ALL SELECT MAX(settled_by) FROM settlements)"
    ).fetchone()
    return (row[0] or 0) + 1


def create_user(conn: sqlite3.Connection, name: str, password_hash: str | None = None) -> int:
    """Insert a user and seed the default categories (inside the caller's transaction)."""
    try:
        cur = conn.execute(
            "INSERT INTO users(id, name, password_hash) VALUES (?, ?, ?)",
            (_next_user_id(conn), name, password_hash),
        )
    except sqlite3.IntegrityError as exc:
        raise DataError(f"A user called '{name}' already exists.") from exc
    user_id = int(cur.lastrowid)
    for top_name, kind, subs in categories.DEFAULT_CATEGORIES:
        top_id = categories.insert_category(conn, user_id, top_name, None, kind)
        for sub_name in subs:
            categories.insert_category(conn, user_id, sub_name, top_id, kind)
    return user_id


def add_user(name: str, password: str) -> int:
    """Create a user with a password and the default categories; return the new user id."""
    name = (name or "").strip()
    if not name:
        raise DataError("Please enter a name.")
    hashed = hash_password(_validate_password(password))
    with connect() as conn:
        return create_user(conn, name, hashed)


def delete_user(user_id: int) -> None:
    """Delete a user and everything they own (transactions, categories). Cannot be undone.

    Deleting the last user is allowed; the app then shows the first-run setup card.
    Refused while the user has unsettled shared balances or pending shared items; once
    settled, their shared records stay with the other users.
    """
    with connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            raise DataError("User not found.")
        shared_transactions.ensure_can_leave(conn, user_id)
        # Their personal copies are deleted below, so unlink them from the shared records first.
        conn.execute("UPDATE shared_participants SET transaction_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM pending_transactions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM recurring WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
        # Subcategories first, since they reference their parents.
        conn.execute("DELETE FROM categories WHERE user_id = ? AND parent_id IS NOT NULL", (user_id,))
        conn.execute("DELETE FROM categories WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
