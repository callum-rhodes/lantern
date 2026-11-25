"""Table definitions, upgrades for older databases, and ``init_db()``."""
from __future__ import annotations

import sqlite3

from db import categories
from db.core import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    password_hash TEXT
);

CREATE TABLE IF NOT EXISTS categories (
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id),
    name      TEXT NOT NULL,
    parent_id INTEGER REFERENCES categories(id),
    kind      TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    -- Position among siblings (same parent), lowest first. Set on the Categories page.
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- Unique on (user, parent, name). COALESCE makes top-level categories
-- (parent_id NULL) unique too, because SQLite treats NULLs as distinct.
CREATE UNIQUE INDEX IF NOT EXISTS ux_categories_user_parent_name
    ON categories(user_id, COALESCE(parent_id, 0), name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    date         TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    category_id  INTEGER NOT NULL REFERENCES categories(id),
    description  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_date
    ON transactions(user_id, date);
CREATE INDEX IF NOT EXISTS idx_transactions_user_category
    ON transactions(user_id, category_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    base       TEXT NOT NULL,
    target     TEXT NOT NULL,
    rate       REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (base, target)
);

CREATE TABLE IF NOT EXISTS recurring (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    description  TEXT NOT NULL DEFAULT '',
    amount_cents INTEGER NOT NULL,
    category_id  INTEGER NOT NULL REFERENCES categories(id),
    every_n      INTEGER NOT NULL DEFAULT 1,
    unit         TEXT NOT NULL CHECK (unit IN ('day', 'week', 'month')),
    start_date   TEXT NOT NULL,      -- occurrence number 0
    generated    INTEGER NOT NULL DEFAULT 0,  -- occurrences already turned into pending items
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pending_transactions (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    recurring_id INTEGER NOT NULL REFERENCES recurring(id),
    due_date     TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    category_id  INTEGER NOT NULL REFERENCES categories(id),
    description  TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_pending_rule_date
    ON pending_transactions(recurring_id, due_date);

CREATE TABLE IF NOT EXISTS shared_transactions (
    id          INTEGER PRIMARY KEY,
    creator_id  INTEGER NOT NULL,
    date        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    total_cents INTEGER NOT NULL CHECK (total_cents > 0),
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'rejected')),
    rejected_by INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_participants (
    id             INTEGER PRIMARY KEY,
    shared_id      INTEGER NOT NULL REFERENCES shared_transactions(id),
    user_id        INTEGER NOT NULL,
    paid_cents     INTEGER NOT NULL CHECK (paid_cents >= 0),
    owed_cents     INTEGER NOT NULL CHECK (owed_cents >= 0),
    status         TEXT NOT NULL CHECK (status IN ('accepted', 'pending')),
    transaction_id INTEGER REFERENCES transactions(id),
    UNIQUE (shared_id, user_id),
    CHECK (paid_cents + owed_cents > 0)
);

CREATE INDEX IF NOT EXISTS idx_shared_participants_user
    ON shared_participants(user_id);
CREATE INDEX IF NOT EXISTS idx_shared_participants_txn
    ON shared_participants(transaction_id);

CREATE TABLE IF NOT EXISTS settlements (
    id           INTEGER PRIMARY KEY,
    from_user_id INTEGER NOT NULL,
    to_user_id   INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    settled_by   INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    CHECK (from_user_id <> to_user_id)
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade databases created by older versions of the app."""
    user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "password_hash" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(categories)")}
    if "sort_order" not in columns:
        conn.execute("ALTER TABLE categories ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        # Number existing categories alphabetically within each parent, so nothing
        # visibly changes until the user starts rearranging.
        counters: dict[tuple[int, int | None], int] = {}
        rows = conn.execute(
            "SELECT id, user_id, parent_id FROM categories "
            "ORDER BY user_id, COALESCE(parent_id, 0), name COLLATE NOCASE"
        ).fetchall()
        for row in rows:
            key = (row["user_id"], row["parent_id"])
            counters[key] = counters.get(key, 0) + 1
            conn.execute("UPDATE categories SET sort_order = ? WHERE id = ?", (counters[key], row["id"]))

    # Transactions filed directly on a top-level category move to its 'Other' subcategory.
    for row in conn.execute(
        "SELECT DISTINCT t.user_id, c.id, c.kind FROM transactions t "
        "JOIN categories c ON c.id = t.category_id WHERE c.parent_id IS NULL"
    ).fetchall():
        other = categories.ensure_other(conn, row["user_id"], row["id"], row["kind"])
        conn.execute(
            "UPDATE transactions SET category_id = ? WHERE category_id = ? AND user_id = ?",
            (other, row["id"], row["user_id"]),
        )


def init_db() -> None:
    """Create all tables and indexes if they do not exist yet, and upgrade old databases."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")  # nicer with several browsers at once
        conn.executescript(SCHEMA)
        _migrate(conn)
