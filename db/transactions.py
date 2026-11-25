"""Transactions: add, edit, delete, list, and the helpers used by CSV import."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from db import categories
from db.core import DataError, all_rows, connect


def check_transaction(
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


def reject_if_shared(conn: sqlite3.Connection, transaction_id: int) -> None:
    """Personal copies of shared transactions can't be edited or deleted on their own."""
    if conn.execute(
        "SELECT 1 FROM shared_participants WHERE transaction_id = ?", (transaction_id,)
    ).fetchone():
        raise DataError(
            "This transaction is part of a shared split. Manage it on the Shared Transactions page."
        )


def add_transaction(
    user_id: int, tx_date: str, amount_cents: int, category_id: int, description: str = ""
) -> int:
    """Insert a transaction (signed ``amount_cents``) and return its id."""
    with connect() as conn:
        category_id = categories.resolve_category(conn, user_id, category_id)
        iso = check_transaction(conn, user_id, tx_date, amount_cents, category_id)
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
        reject_if_shared(conn, transaction_id)
        category_id = categories.resolve_category(conn, user_id, category_id)
        iso = check_transaction(conn, user_id, tx_date, amount_cents, category_id)
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
        reject_if_shared(conn, transaction_id)
        cur = conn.execute(
            "DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id)
        )
        if cur.rowcount == 0:
            raise DataError("Transaction not found.")


def _like_escape(text: str) -> str:
    """Escape LIKE wildcards so user text is matched literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        "       c.name AS category_name, c.parent_id, p.name AS parent_name, c.kind, "
        "       sp.shared_id AS shared_id "
        "FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN categories p ON p.id = c.parent_id "
        "LEFT JOIN shared_participants sp ON sp.transaction_id = t.id "
        "WHERE t.user_id = ?"
    )
    params: list[Any] = [user_id]
    if date_from:
        sql += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND t.date <= ?"
        params.append(date_to)
    clause, clause_params = categories.category_clause(category_id)
    sql += clause
    params += clause_params
    if search and search.strip():
        sql += " AND t.description LIKE ? ESCAPE '\\'"
        params.append(f"%{_like_escape(search.strip())}%")
    sql += " ORDER BY t.date DESC, t.id DESC"
    with connect() as conn:
        return all_rows(conn.execute(sql, params))


def transaction_count(user_id: int) -> int:
    """Number of transactions the user has recorded."""
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ?", (user_id,)
        ).fetchone()[0]


# --------------------------------------------------------------------------
# CSV import helpers
# --------------------------------------------------------------------------

def description_category_map(user_id: int) -> dict[tuple[str, str], int]:
    """Most-used category per (lower-cased description, kind), for matching imported rows."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT LOWER(TRIM(t.description)) AS d, c.kind, t.category_id, COUNT(*) AS n "
            "FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE t.user_id = ? AND TRIM(t.description) <> '' "
            "GROUP BY d, c.kind, t.category_id ORDER BY n ASC",  # most common ends up last, wins
            (user_id,),
        ).fetchall()
    return {(r["d"], r["kind"]): r["category_id"] for r in rows}


def _existing_keys(
    conn: sqlite3.Connection, user_id: int, date_from: str, date_to: str
) -> set[tuple[str, int, int]]:
    """(date, amount_cents, category_id) of existing transactions in a date range."""
    return {
        (r["date"], r["amount_cents"], r["category_id"])
        for r in conn.execute(
            "SELECT date, amount_cents, category_id FROM transactions "
            "WHERE user_id = ? AND date >= ? AND date <= ?",
            (user_id, date_from, date_to),
        )
    }


def existing_keys(user_id: int, date_from: str, date_to: str) -> set[tuple[str, int, int]]:
    """Used by the import preview to flag rows that already exist."""
    with connect() as conn:
        return _existing_keys(conn, user_id, date_from, date_to)


def other_subcategory_map(user_id: int) -> dict[int, int]:
    """{top-level category id: id of its 'Other' subcategory} for those that have one."""
    with connect() as conn:
        return {
            r["parent_id"]: r["id"]
            for r in conn.execute(
                "SELECT id, parent_id FROM categories "
                "WHERE user_id = ? AND parent_id IS NOT NULL AND name = ? COLLATE NOCASE",
                (user_id, categories.OTHER_NAME),
            )
        }


def import_transactions(
    user_id: int, rows: list[tuple[str, int, int, str]], skip_duplicates: bool = False
) -> dict[str, int]:
    """Insert many (date, signed cents, category_id, description) rows in one transaction.

    With ``skip_duplicates`` a row is skipped if a transaction with the same date, amount
    and category already existed before the import. Any invalid row cancels the whole import.
    """
    added = skipped = 0
    with connect() as conn:
        existing: set[tuple[str, int, int]] = set()
        if skip_duplicates and rows:
            existing = _existing_keys(conn, user_id, min(r[0] for r in rows), max(r[0] for r in rows))
        for tx_date, cents, category_id, description in rows:
            category_id = categories.resolve_category(conn, user_id, category_id)
            iso = check_transaction(conn, user_id, tx_date, cents, category_id)
            if (iso, cents, category_id) in existing:
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO transactions(user_id, date, amount_cents, category_id, description) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, iso, cents, category_id, (description or "").strip()),
            )
            added += 1
    return {"added": added, "skipped": skipped}
