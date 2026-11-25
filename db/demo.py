"""Demo data: a user with a few years of random but plausible transactions."""
from __future__ import annotations

import calendar
import logging
import random
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date

from db import categories, users
from db.core import connect

log = logging.getLogger(__name__)

# (category, subcategory, min per month, max per month, min cents, max cents)
_DEMO_EXPENSES: list[tuple[str, str, int, int, int, int]] = [
    ("Home", "Rent/Mortgage", 1, 1, 8000, 14000),
    ("Food", "Groceries", 6, 10, 1500, 9000),
    ("Food", "Eating out", 2, 6, 800, 6000),
    ("Transport", "Fuel", 1, 3, 3000, 7000),
    ("Transport", "Public transport", 3, 8, 250, 2500),
    ("Leisure", "Events", 1, 4, 500, 4000),
    ("Holiday", "Other", 0, 1, 20000, 120000),
    ("Health", "Medical", 0, 1, 1000, 8000),
    ("Health", "Fitness", 1, 1, 3000, 3500),
    ("Shopping", "Clothing", 0, 2, 2000, 9000),
    ("Shopping", "Other", 0, 2, 1000, 8000),
]

_DEMO_DESCRIPTIONS: dict[str, list[str]] = {
    "Groceries": ["Tesco", "Sainsbury's", "Aldi", "Co-op"],
    "Eating out": ["Lunch", "Dinner out", "Coffee", "Takeaway"],
    "Fuel": ["Petrol", "Petrol station"],
    "Public transport": ["Train ticket", "Bus fare", "Tube top-up"],
    "Events": ["Cinema", "Streaming", "Concert"],
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
    top_id = top["id"] if top else categories.insert_category(conn, user_id, top_name, None, kind)
    sub = conn.execute(
        "SELECT id FROM categories WHERE user_id = ? AND parent_id = ? "
        "AND name = ? COLLATE NOCASE",
        (user_id, top_id, sub_name),
    ).fetchone()
    return sub["id"] if sub else categories.insert_category(conn, user_id, sub_name, top_id, kind)


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
            (on(1), -95000, "Home", "Rent/Mortgage", "expense", "Rent"),
        ]
        if rng.random() < 0.15:
            items.append((on(rng.randint(1, 28)), rng.randint(5000, 30000),
                          "Income", "Other", "income", "Side income"))
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
        user_id = row["id"] if row else users.create_user(conn, name)
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
