"""Aggregations for the overview page (all values in base-currency minor units).

Income and expense are decided by the category's kind. Expenses are returned
as positive numbers (a refund in an expense category reduces them).
"""
from __future__ import annotations

from typing import Any

from db import categories
from db.core import all_rows, connect

_INCOME_SQL = "COALESCE(SUM(CASE WHEN c.kind = 'income' THEN t.amount_cents END), 0)"
_EXPENSE_SQL = "COALESCE(-SUM(CASE WHEN c.kind = 'expense' THEN t.amount_cents END), 0)"


def _year_bounds(year: int) -> tuple[str, str]:
    """ISO date range [start, end) covering one calendar year."""
    return f"{year:04d}-01-01", f"{year + 1:04d}-01-01"


def _label_sql(level: str) -> str:
    """SQL for the row label. Only ever one of two fixed strings, never user input."""
    if level == "category":
        return "COALESCE(p.name, c.name)"
    if level == "subcategory":
        return "CASE WHEN p.name IS NULL THEN c.name ELSE p.name || ' > ' || c.name END"
    raise ValueError(f"Unknown level: {level}")


def available_years(user_id: int) -> list[int]:
    """Years that contain at least one transaction, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT CAST(strftime('%Y', date) AS INTEGER) AS y "
            "FROM transactions WHERE user_id = ? ORDER BY y",
            (user_id,),
        ).fetchall()
    return [r["y"] for r in rows]


def period_summary(user_id: int, year: int | None, category_id: int | None = None) -> dict[str, Any]:
    """Income, expenses, net and savings rate for one year (or all years if year is None)."""
    clause, clause_params = categories.category_clause(category_id)
    sql = (
        f"SELECT {_INCOME_SQL} AS income, {_EXPENSE_SQL} AS expenses "
        "FROM transactions t JOIN categories c ON c.id = t.category_id "
        "WHERE t.user_id = ?"
    )
    params: list[Any] = [user_id]
    if year is not None:
        start, end = _year_bounds(year)
        sql += " AND t.date >= ? AND t.date < ?"
        params += [start, end]
    with connect() as conn:
        row = conn.execute(sql + clause, [*params, *clause_params]).fetchone()
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
    if kind not in categories.KINDS:
        raise ValueError(f"Unknown kind: {kind}")
    label_sql = _label_sql(level)
    sign = -1 if kind == "expense" else 1
    start, end = _year_bounds(year)
    clause, clause_params = categories.category_clause(category_id)
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
        return all_rows(conn.execute(sql, [sign, user_id, start, end, kind, *clause_params]))


def yearly_by_category(
    user_id: int, kind: str, level: str = "category", category_id: int | None = None
) -> list[dict[str, Any]]:
    """Like ``monthly_by_category`` but one total per calendar year, across all years.

    The year is returned under the key 'month' (e.g. '2025') so the overview page
    can treat years and months the same way.
    """
    if kind not in categories.KINDS:
        raise ValueError(f"Unknown kind: {kind}")
    label_sql = _label_sql(level)
    sign = -1 if kind == "expense" else 1
    clause, clause_params = categories.category_clause(category_id)
    sql = (
        f"SELECT strftime('%Y', t.date) AS month, {label_sql} AS label, "
        "       SUM(t.amount_cents) * ? AS cents "
        "FROM transactions t "
        "JOIN categories c ON c.id = t.category_id "
        "LEFT JOIN categories p ON p.id = c.parent_id "
        "WHERE t.user_id = ? AND c.kind = ?"
        + clause
        + " GROUP BY strftime('%Y', t.date), label ORDER BY month, label"
    )
    with connect() as conn:
        return all_rows(conn.execute(sql, [sign, user_id, kind, *clause_params]))


def monthly_income_expense(
    user_id: int, year: int, category_id: int | None = None
) -> list[dict[str, Any]]:
    """Income and expenses per month (only months that have data)."""
    start, end = _year_bounds(year)
    clause, clause_params = categories.category_clause(category_id)
    with connect() as conn:
        return all_rows(conn.execute(
            f"SELECT strftime('%Y-%m', t.date) AS month, "
            f"       {_INCOME_SQL} AS income, {_EXPENSE_SQL} AS expenses "
            "FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE t.user_id = ? AND t.date >= ? AND t.date < ?" + clause
            + " GROUP BY strftime('%Y-%m', t.date) ORDER BY month",
            [user_id, start, end, *clause_params],
        ))


def yearly_totals(user_id: int, category_id: int | None = None) -> list[dict[str, Any]]:
    """Income and expenses per year across all years (optionally for one category)."""
    clause, clause_params = categories.category_clause(category_id)
    with connect() as conn:
        return all_rows(conn.execute(
            f"SELECT CAST(strftime('%Y', t.date) AS INTEGER) AS year, "
            f"       {_INCOME_SQL} AS income, {_EXPENSE_SQL} AS expenses "
            "FROM transactions t JOIN categories c ON c.id = t.category_id "
            "WHERE t.user_id = ?" + clause
            + " GROUP BY strftime('%Y', t.date) ORDER BY year",
            [user_id, *clause_params],
        ))
