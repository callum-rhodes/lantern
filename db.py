"""Database layer: connection handling, schema creation and every SQL query.

All other modules call functions from here; no SQL lives anywhere else.

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

import calendar
import logging
import os
import random
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "finance.db"
DEFAULT_BASE_CURRENCY = "GBP"
KINDS = ("income", "expense")

# (category, kind, [subcategories]) created for every new user.
DEFAULT_CATEGORIES: list[tuple[str, str, list[str]]] = [
    ("Housing", "expense", ["Rent", "Utilities"]),
    ("Food", "expense", ["Groceries", "Eating out"]),
    ("Transport", "expense", ["Fuel", "Public transport"]),
    ("Leisure", "expense", ["Entertainment", "Holidays"]),
    ("Health", "expense", ["Medical", "Fitness"]),
    ("Shopping", "expense", ["Clothing", "Household"]),
    ("Income", "income", ["Salary", "Other income"]),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
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
"""


class DataError(Exception):
    """A problem with the data that can be explained to the user."""


# --------------------------------------------------------------------------
# Connection handling
# --------------------------------------------------------------------------

def get_db_path() -> Path:
    """Return the database file path (``FINANCE_DB_PATH`` overrides the default)."""
    return Path(os.environ.get("FINANCE_DB_PATH", DEFAULT_DB_PATH))


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


def _migrate(conn: sqlite3.Connection) -> None:
    """Upgrade databases created by older versions of the app."""
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


def init_db() -> None:
    """Create all tables and indexes if they do not exist yet, and upgrade old databases."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")  # nicer with several browsers at once
        conn.executescript(SCHEMA)
        _migrate(conn)


def _all(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """Convert every row of a cursor to a plain dict."""
    return [dict(row) for row in cursor.fetchall()]


def _one(cursor: sqlite3.Cursor) -> dict[str, Any] | None:
    """Convert the next row of a cursor to a dict (or None)."""
    row = cursor.fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def get_setting(key: str, default: str | None = None) -> str | None:
    """Read one setting value."""
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Create or update one setting."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def is_base_currency_set() -> bool:
    """True once the base currency has been chosen (false on first run)."""
    return get_setting("base_currency") is not None


def get_base_currency() -> str:
    """Return the base currency code (default GBP)."""
    return get_setting("base_currency", DEFAULT_BASE_CURRENCY) or DEFAULT_BASE_CURRENCY


def set_base_currency(code: str) -> None:
    """Store the base currency; all recorded amounts are in this currency."""
    code = (code or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise DataError("Currency codes are three letters, e.g. GBP.")
    set_setting("base_currency", code)


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

def list_users() -> list[dict[str, Any]]:
    """All users, ordered by name."""
    with connect() as conn:
        return _all(conn.execute("SELECT id, name FROM users ORDER BY name COLLATE NOCASE"))


def get_user(user_id: int) -> dict[str, Any] | None:
    """One user by id."""
    with connect() as conn:
        return _one(conn.execute("SELECT id, name FROM users WHERE id = ?", (user_id,)))


def _create_user(conn: sqlite3.Connection, name: str) -> int:
    """Insert a user and seed the default categories (inside the caller's transaction)."""
    try:
        cur = conn.execute("INSERT INTO users(name) VALUES (?)", (name,))
    except sqlite3.IntegrityError as exc:
        raise DataError(f"A user called '{name}' already exists.") from exc
    user_id = int(cur.lastrowid)
    for top_name, kind, subs in DEFAULT_CATEGORIES:
        top_id = _insert_category(conn, user_id, top_name, None, kind)
        for sub_name in subs:
            _insert_category(conn, user_id, sub_name, top_id, kind)
    return user_id


def add_user(name: str) -> int:
    """Create a user with the default categories; return the new user id."""
    name = (name or "").strip()
    if not name:
        raise DataError("Please enter a name.")
    with connect() as conn:
        return _create_user(conn, name)


# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------

def _insert_category(
    conn: sqlite3.Connection, user_id: int, name: str, parent_id: int | None, kind: str
) -> int:
    """Raw insert, placed after its siblings; callers are responsible for validation."""
    cur = conn.execute(
        "INSERT INTO categories(user_id, name, parent_id, kind, sort_order) "
        "VALUES (?, ?, ?, ?, ("
        "  SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories "
        "  WHERE user_id = ? AND COALESCE(parent_id, 0) = COALESCE(?, 0)))",
        (user_id, name, parent_id, kind, user_id, parent_id),
    )
    return int(cur.lastrowid)


def get_category(user_id: int, category_id: int) -> dict[str, Any] | None:
    """One category (with its parent's name, if any)."""
    with connect() as conn:
        return _one(conn.execute(
            "SELECT c.id, c.name, c.parent_id, c.kind, p.name AS parent_name "
            "FROM categories c LEFT JOIN categories p ON p.id = c.parent_id "
            "WHERE c.id = ? AND c.user_id = ?",
            (category_id, user_id),
        ))


def list_categories(user_id: int) -> list[dict[str, Any]]:
    """Every category of the user in the user's own order.

    Ordered by kind, then top-level position, with each category followed by its
    subcategories in their own order (see ``move_category``).
    """
    with connect() as conn:
        return _all(conn.execute(
            "SELECT c.id, c.name, c.parent_id, c.kind, p.name AS parent_name "
            "FROM categories c LEFT JOIN categories p ON p.id = c.parent_id "
            "WHERE c.user_id = ? "
            "ORDER BY c.kind, COALESCE(p.sort_order, c.sort_order), "
            "         CASE WHEN c.parent_id IS NULL THEN 0 ELSE 1 END, "
            "         c.sort_order, c.name COLLATE NOCASE",
            (user_id,),
        ))


def get_category_tree(user_id: int) -> list[dict[str, Any]]:
    """Top-level categories, each with a ``children`` list of subcategories."""
    rows = list_categories(user_id)
    tops = [{**r, "children": []} for r in rows if r["parent_id"] is None]
    by_id = {t["id"]: t for t in tops}
    for row in rows:
        if row["parent_id"] is not None and row["parent_id"] in by_id:
            by_id[row["parent_id"]]["children"].append(row)
    return tops


def list_top_level(user_id: int, kind: str | None = None) -> list[dict[str, Any]]:
    """Top-level categories, optionally only of one kind."""
    return [t for t in get_category_tree(user_id) if kind is None or t["kind"] == kind]


def list_subcategories(user_id: int, parent_id: int) -> list[dict[str, Any]]:
    """Subcategories of one top-level category."""
    with connect() as conn:
        return _all(conn.execute(
            "SELECT id, name, parent_id, kind FROM categories "
            "WHERE user_id = ? AND parent_id = ? ORDER BY sort_order, name COLLATE NOCASE",
            (user_id, parent_id),
        ))


def category_options(
    user_id: int, kind: str | None = None, exclude_id: int | None = None
) -> dict[int, str]:
    """Flat ``{id: label}`` mapping for dropdowns, e.g. 'Food' and 'Food > Groceries'."""
    options: dict[int, str] = {}
    for top in get_category_tree(user_id):
        if kind is not None and top["kind"] != kind:
            continue
        if top["id"] != exclude_id:
            options[top["id"]] = top["name"]
        for child in top["children"]:
            if child["id"] != exclude_id:
                options[child["id"]] = f"{top['name']} > {child['name']}"
    return options


def add_category(user_id: int, name: str, parent_id: int | None = None, kind: str | None = None) -> int:
    """Add a category or subcategory and return its id.

    Subcategories inherit the kind of their parent; top-level categories need
    ``kind`` ('income' or 'expense'). A subcategory cannot have children.
    """
    name = (name or "").strip()
    if not name:
        raise DataError("Please enter a name.")
    with connect() as conn:
        if parent_id is not None:
            parent = conn.execute(
                "SELECT id, parent_id, kind FROM categories WHERE id = ? AND user_id = ?",
                (parent_id, user_id),
            ).fetchone()
            if parent is None:
                raise DataError("The parent category no longer exists.")
            if parent["parent_id"] is not None:
                raise DataError("Subcategories cannot have their own subcategories (two levels maximum).")
            kind = parent["kind"]
        elif kind not in KINDS:
            raise DataError("Please choose whether the category is income or expense.")
        try:
            return _insert_category(conn, user_id, name, parent_id, kind)  # type: ignore[arg-type]
        except sqlite3.IntegrityError as exc:
            raise DataError(f"'{name}' already exists here.") from exc


def rename_category(user_id: int, category_id: int, new_name: str) -> None:
    """Rename a category or subcategory."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise DataError("Please enter a name.")
    with connect() as conn:
        try:
            cur = conn.execute(
                "UPDATE categories SET name = ? WHERE id = ? AND user_id = ?",
                (new_name, category_id, user_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DataError(f"'{new_name}' already exists here.") from exc
        if cur.rowcount == 0:
            raise DataError("Category not found.")


def move_category(user_id: int, category_id: int, direction: int) -> bool:
    """Move a category one step up (``direction=-1``) or down (``+1``) among its siblings.

    Top-level categories move among those of the same kind; subcategories move
    within their parent. Returns False if it is already first/last.
    """
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 (up) or 1 (down)")
    with connect() as conn:
        cat = conn.execute(
            "SELECT id, parent_id, kind FROM categories WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        ).fetchone()
        if cat is None:
            raise DataError("Category not found.")
        siblings = [row["id"] for row in conn.execute(
            "SELECT id FROM categories "
            "WHERE user_id = ? AND COALESCE(parent_id, 0) = COALESCE(?, 0) AND kind = ? "
            "ORDER BY sort_order, name COLLATE NOCASE",
            (user_id, cat["parent_id"], cat["kind"]),
        )]
        index = siblings.index(category_id)
        target = index + direction
        if not 0 <= target < len(siblings):
            return False
        siblings[index], siblings[target] = siblings[target], siblings[index]
        # Renumber the whole group so the order stays valid even if values were equal.
        conn.executemany(
            "UPDATE categories SET sort_order = ? WHERE id = ? AND user_id = ?",
            [(position, cid, user_id) for position, cid in enumerate(siblings, start=1)],
        )
    return True


def category_usage(user_id: int, category_id: int) -> dict[str, int]:
    """How many transactions and subcategories use a category."""
    with connect() as conn:
        txns = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        ).fetchone()[0]
        subs = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE user_id = ? AND parent_id = ?",
            (user_id, category_id),
        ).fetchone()[0]
    return {"transactions": txns, "subcategories": subs}


def delete_category(user_id: int, category_id: int, reassign_to: int | None = None) -> None:
    """Delete a category.

    Blocked while it still has subcategories. If it has transactions, pass
    ``reassign_to`` (another category of the same kind) to move them first;
    otherwise a clear error is raised.
    """
    with connect() as conn:
        cat = conn.execute(
            "SELECT id, name, kind FROM categories WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        ).fetchone()
        if cat is None:
            raise DataError("Category not found.")
        subs = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE user_id = ? AND parent_id = ?",
            (user_id, category_id),
        ).fetchone()[0]
        if subs:
            raise DataError(f"'{cat['name']}' still has subcategories. Delete those first.")
        txns = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        ).fetchone()[0]
        if txns:
            if reassign_to is None:
                raise DataError(
                    f"'{cat['name']}' has {txns} transaction(s). "
                    "Move them to another category before deleting it."
                )
            target = conn.execute(
                "SELECT id, kind FROM categories WHERE id = ? AND user_id = ?",
                (reassign_to, user_id),
            ).fetchone()
            if target is None or target["id"] == category_id:
                raise DataError("Choose a different category to move the transactions to.")
            if target["kind"] != cat["kind"]:
                raise DataError("Transactions can only move to a category of the same kind (income/expense).")
            conn.execute(
                "UPDATE transactions SET category_id = ? WHERE category_id = ? AND user_id = ?",
                (reassign_to, category_id, user_id),
            )
        conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", (category_id, user_id))


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

def _check_transaction(
    conn: sqlite3.Connection, user_id: int, tx_date: str, amount_cents: int, category_id: int
) -> str:
    """Validate transaction fields; return the normalised ISO date."""
    try:
        iso = date.fromisoformat(tx_date).isoformat()
    except (TypeError, ValueError) as exc:
        raise DataError("Please enter a valid date (YYYY-MM-DD).") from exc
    if not isinstance(amount_cents, int) or amount_cents == 0:
        raise DataError("The amount must be a non-zero number.")
    row = conn.execute(
        "SELECT kind FROM categories WHERE id = ? AND user_id = ?", (category_id, user_id)
    ).fetchone()
    if row is None:
        raise DataError("Please choose a valid category.")
    if (row["kind"] == "income") != (amount_cents > 0):
        raise DataError(
            "Income categories take income and expense categories take expenses. "
            "Check the type toggle and the category."
        )
    return iso


def add_transaction(
    user_id: int, tx_date: str, amount_cents: int, category_id: int, description: str = ""
) -> int:
    """Insert a transaction (signed ``amount_cents``) and return its id."""
    with connect() as conn:
        iso = _check_transaction(conn, user_id, tx_date, amount_cents, category_id)
        cur = conn.execute(
            "INSERT INTO transactions(user_id, date, amount_cents, category_id, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, iso, amount_cents, category_id, (description or "").strip()),
        )
        return int(cur.lastrowid)


def update_transaction(
    user_id: int, transaction_id: int, tx_date: str, amount_cents: int,
    category_id: int, description: str = "",
) -> None:
    """Replace the fields of an existing transaction."""
    with connect() as conn:
        iso = _check_transaction(conn, user_id, tx_date, amount_cents, category_id)
        cur = conn.execute(
            "UPDATE transactions SET date = ?, amount_cents = ?, category_id = ?, description = ? "
            "WHERE id = ? AND user_id = ?",
            (iso, amount_cents, category_id, (description or "").strip(), transaction_id, user_id),
        )
        if cur.rowcount == 0:
            raise DataError("Transaction not found.")


def delete_transaction(user_id: int, transaction_id: int) -> None:
    """Delete one transaction."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id)
        )
        if cur.rowcount == 0:
            raise DataError("Transaction not found.")


def _like_escape(text: str) -> str:
    """Escape LIKE wildcards so user text is matched literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _category_clause(category_id: int | None) -> tuple[str, list[Any]]:
    """SQL fragment matching a category and its subcategories (aliases: c)."""
    if category_id is None:
        return "", []
    return " AND (c.id = ? OR c.parent_id = ?)", [category_id, category_id]


def list_transactions(
    user_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    category_id: int | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Transactions newest first. Filtering by a category includes its subcategories."""
    sql = (
        "SELECT t.id, t.date, t.description, t.amount_cents, t.category_id, "
        "       c.name AS category_name, c.parent_id, p.name AS parent_name, c.kind "
        "FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN categories p ON p.id = c.parent_id "
        "WHERE t.user_id = ?"
    )
    params: list[Any] = [user_id]
    if date_from:
        sql += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND t.date <= ?"
        params.append(date_to)
    clause, clause_params = _category_clause(category_id)
    sql += clause
    params += clause_params
    if search and search.strip():
        sql += " AND t.description LIKE ? ESCAPE '\\'"
        params.append(f"%{_like_escape(search.strip())}%")
    sql += " ORDER BY t.date DESC, t.id DESC"
    with connect() as conn:
        return _all(conn.execute(sql, params))


def transaction_count(user_id: int) -> int:
    """Number of transactions the user has recorded."""
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ?", (user_id,)
        ).fetchone()[0]


# --------------------------------------------------------------------------
# Aggregations for the overview page (all values in base-currency minor units)
# --------------------------------------------------------------------------
# Income and expense are decided by the category's kind. Expenses are returned
# as positive numbers (a refund in an expense category reduces them).

_INCOME_SQL = "COALESCE(SUM(CASE WHEN c.kind = 'income' THEN t.amount_cents END), 0)"
_EXPENSE_SQL = "COALESCE(-SUM(CASE WHEN c.kind = 'expense' THEN t.amount_cents END), 0)"


def _year_bounds(year: int) -> tuple[str, str]:
    """ISO date range [start, end) covering one calendar year."""
    return f"{year:04d}-01-01", f"{year + 1:04d}-01-01"


def available_years(user_id: int) -> list[int]:
    """Years that contain at least one transaction, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT CAST(strftime('%Y', date) AS INTEGER) AS y "
            "FROM transactions WHERE user_id = ? ORDER BY y",
            (user_id,),
        ).fetchall()
    return [r["y"] for r in rows]


def period_summary(user_id: int, year: int, category_id: int | None = None) -> dict[str, Any]:
    """Income, expenses, net and savings rate (None if no income) for one year."""
    start, end = _year_bounds(year)
    clause, clause_params = _category_clause(category_id)
    with connect() as conn:
        row = conn.execute(
            f"SELECT {_INCOME_SQL} AS income, {_EXPENSE_SQL} AS expenses "
            "FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE t.user_id = ? AND t.date >= ? AND t.date < ?" + clause,
            [user_id, start, end, *clause_params],
        ).fetchone()
    income, expenses = row["income"], row["expenses"]
    net = income - expenses
    return {
        "income": income,
        "expenses": expenses,
        "net": net,
        "savings_rate": (net / income) if income > 0 else None,
    }


def monthly_by_category(
    user_id: int, year: int, kind: str, level: str = "category", category_id: int | None = None
) -> list[dict[str, Any]]:
    """Totals per month and category for one year.

    ``level`` is 'category' (subcategories rolled up into their parent) or
    'subcategory' (labels like 'Food > Groceries'). ``kind`` is 'income' or
    'expense'. Returns ``[{'month': '2025-03', 'label': 'Food', 'cents': 1234}]``
    with positive numbers.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown kind: {kind}")
    if level == "category":
        label_sql = "COALESCE(p.name, c.name)"
    elif level == "subcategory":
        label_sql = "CASE WHEN p.name IS NULL THEN c.name ELSE p.name || ' > ' || c.name END"
    else:
        raise ValueError(f"Unknown level: {level}")
    sign = -1 if kind == "expense" else 1
    start, end = _year_bounds(year)
    clause, clause_params = _category_clause(category_id)
    # label_sql comes from the two fixed strings above, never from user input.
    sql = (
        f"SELECT strftime('%Y-%m', t.date) AS month, {label_sql} AS label, "
        "       SUM(t.amount_cents) * ? AS cents "
        "FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN categories p ON p.id = c.parent_id "
        "WHERE t.user_id = ? AND t.date >= ? AND t.date < ? AND c.kind = ?"
        + clause
        + " GROUP BY strftime('%Y-%m', t.date), label ORDER BY month, label"
    )
    with connect() as conn:
        return _all(conn.execute(sql, [sign, user_id, start, end, kind, *clause_params]))


def monthly_income_expense(
    user_id: int, year: int, category_id: int | None = None
) -> list[dict[str, Any]]:
    """Income and expenses per month (only months that have data)."""
    start, end = _year_bounds(year)
    clause, clause_params = _category_clause(category_id)
    with connect() as conn:
        return _all(conn.execute(
            f"SELECT strftime('%Y-%m', t.date) AS month, "
            f"       {_INCOME_SQL} AS income, {_EXPENSE_SQL} AS expenses "
            "FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE t.user_id = ? AND t.date >= ? AND t.date < ?" + clause
            + " GROUP BY strftime('%Y-%m', t.date) ORDER BY month",
            [user_id, start, end, *clause_params],
        ))


def yearly_totals(user_id: int, category_id: int | None = None) -> list[dict[str, Any]]:
    """Income and expenses per year across all years (optionally for one category)."""
    clause, clause_params = _category_clause(category_id)
    with connect() as conn:
        return _all(conn.execute(
            f"SELECT CAST(strftime('%Y', t.date) AS INTEGER) AS year, "
            f"       {_INCOME_SQL} AS income, {_EXPENSE_SQL} AS expenses "
            "FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE t.user_id = ?" + clause
            + " GROUP BY strftime('%Y', t.date) ORDER BY year",
            [user_id, *clause_params],
        ))


# --------------------------------------------------------------------------
# Exchange-rate cache
# --------------------------------------------------------------------------

def save_rates(base: str, rates: dict[str, float], fetched_at: str | None = None) -> str:
    """Replace the cached rates for ``base``; return the fetch timestamp (UTC ISO)."""
    stamp = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("DELETE FROM exchange_rates WHERE base = ?", (base,))
        conn.executemany(
            "INSERT INTO exchange_rates(base, target, rate, fetched_at) VALUES (?, ?, ?, ?)",
            [(base, code, float(rate), stamp) for code, rate in rates.items()],
        )
    return stamp


def load_rates(base: str) -> tuple[dict[str, float], str | None]:
    """Return ``(rates, fetched_at)`` from the cache; ``({}, None)`` if empty."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT target, rate, fetched_at FROM exchange_rates WHERE base = ?", (base,)
        ).fetchall()
    if not rows:
        return {}, None
    return {r["target"]: r["rate"] for r in rows}, max(r["fetched_at"] for r in rows)


# --------------------------------------------------------------------------
# Demo data
# --------------------------------------------------------------------------

# (category, subcategory, min per month, max per month, min cents, max cents)
_DEMO_EXPENSES: list[tuple[str, str, int, int, int, int]] = [
    ("Housing", "Utilities", 1, 1, 8000, 14000),
    ("Food", "Groceries", 6, 10, 1500, 9000),
    ("Food", "Eating out", 2, 6, 800, 6000),
    ("Transport", "Fuel", 1, 3, 3000, 7000),
    ("Transport", "Public transport", 3, 8, 250, 2500),
    ("Leisure", "Entertainment", 1, 4, 500, 4000),
    ("Leisure", "Holidays", 0, 1, 20000, 120000),
    ("Health", "Medical", 0, 1, 1000, 8000),
    ("Health", "Fitness", 1, 1, 3000, 3500),
    ("Shopping", "Clothing", 0, 2, 2000, 9000),
    ("Shopping", "Household", 0, 2, 1000, 8000),
]

_DEMO_DESCRIPTIONS: dict[str, list[str]] = {
    "Groceries": ["Tesco", "Sainsbury's", "Aldi", "Co-op"],
    "Eating out": ["Lunch", "Dinner out", "Coffee", "Takeaway"],
    "Fuel": ["Petrol", "Petrol station"],
    "Public transport": ["Train ticket", "Bus fare", "Tube top-up"],
    "Entertainment": ["Cinema", "Streaming", "Concert"],
}


def _ensure_category(
    conn: sqlite3.Connection, user_id: int, top_name: str, sub_name: str, kind: str
) -> int:
    """Find or create a category/subcategory pair and return the subcategory id."""
    top = conn.execute(
        "SELECT id FROM categories WHERE user_id = ? AND parent_id IS NULL "
        "AND name = ? COLLATE NOCASE",
        (user_id, top_name),
    ).fetchone()
    top_id = top["id"] if top else _insert_category(conn, user_id, top_name, None, kind)
    sub = conn.execute(
        "SELECT id FROM categories WHERE user_id = ? AND parent_id = ? "
        "AND name = ? COLLATE NOCASE",
        (user_id, top_id, sub_name),
    ).fetchone()
    return sub["id"] if sub else _insert_category(conn, user_id, sub_name, top_id, kind)


def _demo_rows(
    rng: random.Random, user_id: int, years: int, category_id: Callable[[str, str, str], int]
) -> Iterator[tuple[int, str, int, int, str]]:
    """Yield random but plausible transaction rows for the last ``years`` years."""
    today = date.today()
    total_months = years * 12
    first_index = today.year * 12 + (today.month - 1) - (total_months - 1)
    for i in range(total_months):
        year, month0 = divmod(first_index + i, 12)
        month = month0 + 1
        last_day = calendar.monthrange(year, month)[1]

        def on(day: int) -> date:
            return date(year, month, min(day, last_day))

        # Fixed monthly items: salary (slowly rising), rent, occasional extra income.
        items: list[tuple[date, int, str, str, str, str]] = [
            (on(25), 260000 + i * 800, "Income", "Salary", "income", "Salary"),
            (on(1), -95000, "Housing", "Rent", "expense", "Rent"),
        ]
        if rng.random() < 0.15:
            items.append((on(rng.randint(1, 28)), rng.randint(5000, 30000),
                          "Income", "Other income", "income", "Side income"))
        for top, sub, lo_n, hi_n, lo_c, hi_c in _DEMO_EXPENSES:
            for _ in range(rng.randint(lo_n, hi_n)):
                text = rng.choice(_DEMO_DESCRIPTIONS.get(sub, [sub]))
                items.append((on(rng.randint(1, 28)), -rng.randint(lo_c, hi_c),
                              top, sub, "expense", text))
        for tx_date, cents, top, sub, kind, text in items:
            if tx_date <= today:
                yield user_id, tx_date.isoformat(), cents, category_id(top, sub, kind), text


def seed_demo_user(name: str = "Demo", years: int = 3, seed: int | None = None) -> int:
    """Create a demo user (if missing) with random transactions; return the user id.

    Does nothing if that user already has transactions, so it is safe to re-run.
    """
    rng = random.Random(seed)
    with connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
        user_id = row["id"] if row else _create_user(conn, name)
        if conn.execute(
            "SELECT 1 FROM transactions WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone():
            return user_id

        cache: dict[tuple[str, str], int] = {}

        def category_id(top: str, sub: str, kind: str) -> int:
            if (top, sub) not in cache:
                cache[(top, sub)] = _ensure_category(conn, user_id, top, sub, kind)
            return cache[(top, sub)]

        rows = list(_demo_rows(rng, user_id, years, category_id))
        conn.executemany(
            "INSERT INTO transactions(user_id, date, amount_cents, category_id, description) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        log.info("Seeded %d demo transactions for user '%s'", len(rows), name)
    return user_id
