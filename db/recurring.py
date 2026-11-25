"""Recurring transactions.

A rule has a start date and an "every N days/weeks/months" interval. Occurrence i
falls on start + i*interval (computed from the start, so a monthly rule starting on
the 31st gives Feb 28 and then Mar 31). Occurrences before today become pending
items; the user accepts (-> real transaction) or rejects them.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from db import categories, transactions
from db.core import DataError, all_rows, connect

UNITS = ("day", "week", "month")


def _add_months(d: date, months: int) -> date:
    year, month0 = divmod(d.year * 12 + d.month - 1 + months, 12)
    last_day = calendar.monthrange(year, month0 + 1)[1]
    return date(year, month0 + 1, min(d.day, last_day))


def _occurrence(start: date, every: int, unit: str, i: int) -> date:
    if unit == "day":
        return start + timedelta(days=every * i)
    if unit == "week":
        return start + timedelta(weeks=every * i)
    return _add_months(start, every * i)


def _check_repeat(every_n: int, unit: str) -> None:
    if not isinstance(every_n, int) or every_n < 1:
        raise DataError("The repeat interval must be a whole number, 1 or more.")
    if unit not in UNITS:
        raise DataError("Please choose days, weeks or months.")


def add_recurring(
    user_id: int, start_date: str, amount_cents: int, category_id: int,
    description: str, every_n: int, unit: str,
) -> int:
    """Create a recurring rule whose first occurrence is ``start_date``."""
    with connect() as conn:
        category_id = categories.resolve_category(conn, user_id, category_id)
        iso = transactions.check_transaction(conn, user_id, start_date, amount_cents, category_id)
        _check_repeat(every_n, unit)
        cur = conn.execute(
            "INSERT INTO recurring(user_id, description, amount_cents, category_id, every_n, unit, start_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, (description or "").strip(), amount_cents, category_id, every_n, unit, iso),
        )
        return int(cur.lastrowid)


def update_recurring(
    user_id: int, rule_id: int, next_date: str, amount_cents: int, category_id: int,
    description: str, every_n: int, unit: str,
) -> None:
    """Change a rule. ``next_date`` becomes its next occurrence; pending items are untouched."""
    with connect() as conn:
        category_id = categories.resolve_category(conn, user_id, category_id)
        iso = transactions.check_transaction(conn, user_id, next_date, amount_cents, category_id)
        _check_repeat(every_n, unit)
        cur = conn.execute(
            "UPDATE recurring SET description = ?, amount_cents = ?, category_id = ?, every_n = ?, "
            "unit = ?, start_date = ?, generated = 0 WHERE id = ? AND user_id = ?",
            ((description or "").strip(), amount_cents, category_id, every_n, unit, iso, rule_id, user_id),
        )
        if cur.rowcount == 0:
            raise DataError("Recurring transaction not found.")


def list_recurring(user_id: int) -> list[dict[str, Any]]:
    """Rules with category info and ``next_date`` (also under 'date', so the form can reuse them)."""
    with connect() as conn:
        rules = all_rows(conn.execute(
            "SELECT r.id, r.description, r.amount_cents, r.category_id, r.every_n, r.unit, "
            "       r.start_date, r.generated, r.active, "
            "       c.name AS category_name, c.parent_id, p.name AS parent_name, c.kind "
            "FROM recurring r JOIN categories c ON c.id = r.category_id "
            "LEFT JOIN categories p ON p.id = c.parent_id "
            "WHERE r.user_id = ? ORDER BY r.active DESC, r.description COLLATE NOCASE",
            (user_id,),
        ))
    for rule in rules:
        nxt = _occurrence(date.fromisoformat(rule["start_date"]), rule["every_n"], rule["unit"], rule["generated"])
        rule["next_date"] = rule["date"] = nxt.isoformat()
    return rules


def set_recurring_active(user_id: int, rule_id: int, active: bool) -> None:
    """Stop or resume a rule. Resuming skips any dates that passed while it was stopped."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM recurring WHERE id = ? AND user_id = ?", (rule_id, user_id)
        ).fetchone()
        if row is None:
            raise DataError("Recurring transaction not found.")
        generated = row["generated"]
        if active:
            start, today = date.fromisoformat(row["start_date"]), date.today()
            while _occurrence(start, row["every_n"], row["unit"], generated) < today:
                generated += 1
        conn.execute(
            "UPDATE recurring SET active = ?, generated = ? WHERE id = ?",
            (1 if active else 0, generated, rule_id),
        )


def delete_recurring(user_id: int, rule_id: int) -> None:
    """Delete a rule and its pending items (accepted transactions are kept)."""
    with connect() as conn:
        conn.execute("DELETE FROM pending_transactions WHERE recurring_id = ? AND user_id = ?",
                     (rule_id, user_id))
        cur = conn.execute("DELETE FROM recurring WHERE id = ? AND user_id = ?", (rule_id, user_id))
        if cur.rowcount == 0:
            raise DataError("Recurring transaction not found.")


def generate_pending(user_id: int) -> int:
    """Create a pending item for every active-rule occurrence dated before today."""
    today, made = date.today(), 0
    with connect() as conn:
        rules = conn.execute(
            "SELECT * FROM recurring WHERE user_id = ? AND active = 1", (user_id,)
        ).fetchall()
        for rule in rules:
            start, i = date.fromisoformat(rule["start_date"]), rule["generated"]
            while (due := _occurrence(start, rule["every_n"], rule["unit"], i)) < today:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_transactions"
                    "(user_id, recurring_id, due_date, amount_cents, category_id, description) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, rule["id"], due.isoformat(), rule["amount_cents"],
                     rule["category_id"], rule["description"]),
                )
                i += 1
                made += 1
            if i != rule["generated"]:
                conn.execute("UPDATE recurring SET generated = ? WHERE id = ?", (i, rule["id"]))
    return made


def list_pending(user_id: int) -> list[dict[str, Any]]:
    """Pending items, oldest first."""
    with connect() as conn:
        return all_rows(conn.execute(
            "SELECT n.id, n.due_date, n.description, n.amount_cents, n.category_id, "
            "       c.name AS category_name, c.parent_id, p.name AS parent_name, c.kind "
            "FROM pending_transactions n JOIN categories c ON c.id = n.category_id "
            "LEFT JOIN categories p ON p.id = c.parent_id "
            "WHERE n.user_id = ? ORDER BY n.due_date, n.id",
            (user_id,),
        ))


def accept_pending(user_id: int, pending_id: int, amount_cents: int | None = None) -> None:
    """Turn a pending item into a real transaction on its due date.

    ``amount_cents`` (signed) overrides the expected amount, e.g. when a bill came in different.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pending_transactions WHERE id = ? AND user_id = ?", (pending_id, user_id)
        ).fetchone()
        if row is None:
            raise DataError("That pending transaction no longer exists.")
        cents = row["amount_cents"] if amount_cents is None else amount_cents
        iso = transactions.check_transaction(conn, user_id, row["due_date"], cents, row["category_id"])
        conn.execute(
            "INSERT INTO transactions(user_id, date, amount_cents, category_id, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, iso, cents, row["category_id"], row["description"]),
        )
        conn.execute("DELETE FROM pending_transactions WHERE id = ?", (pending_id,))


def reject_pending(user_id: int, pending_id: int) -> None:
    """Discard a pending item."""
    with connect() as conn:
        conn.execute("DELETE FROM pending_transactions WHERE id = ? AND user_id = ?",
                     (pending_id, user_id))
