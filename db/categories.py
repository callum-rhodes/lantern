"""Categories and subcategories (two levels maximum)."""
from __future__ import annotations

import sqlite3
from typing import Any

from db.core import DataError, all_rows, connect, one_row

KINDS = ("income", "expense")
OTHER_NAME = "Other"
# SQL: sorts a category's 'Other' subcategory after its siblings (alias c).
_OTHER_LAST = "CASE WHEN c.parent_id IS NOT NULL AND c.name = 'Other' COLLATE NOCASE THEN 1 ELSE 0 END"

# (category, kind, [subcategories]) created for every new user.
DEFAULT_CATEGORIES: list[tuple[str, str, list[str]]] = [
    ('Home', 'expense', ['Rent/Mortgage', 'Gas/Electric', 'Water', 'Internet', 'Phone', 'Insurance', 'Furniture/Appliances', 'Maintenance/Supplies', 'Improvements', 'Other']),
    ('Transport', 'expense', ['Vehicle payments (inc. repairs)', 'Insurance/Tax/MOT', 'Fuel', 'Public transport', 'Parking', 'Fines', 'Other']),
    ('Food', 'expense', ['Groceries', 'Eating out', 'Pub/Bar/Cafe', 'Other']),
    ('Leisure', 'expense', ['Subscriptions', 'Events', 'Sports', 'Games', 'Hobbies', 'Other']),
    ('Health', 'expense', ['Fitness', 'Medical', 'Beauty', 'Other']),
    ('Shopping', 'expense', ['Clothing', 'Toys/Gadgets', 'Gifts', 'Other']),
    ('Holiday', 'expense', ['Transport', 'Hotels', 'Food', 'Shopping', 'Activities', 'Other']),
    ('Pet', 'expense', ['Food/Litter/Treats', 'Vet', 'Toys', 'Boarding', 'Other']),
    ('Work Expenses', 'expense', ['Food', 'Transport', 'Other']),
    ('Miscellaneous', 'expense', ['Charity', 'Education', 'Other']),
    ('Income', 'income', ['Salary', 'Interest', 'Reimbursements', 'Personal Sales', 'Other']),
]


def is_other(category: dict[str, Any]) -> bool:
    """True for the built-in 'Other' subcategory (protected, always last)."""
    return (
        category.get("parent_id") is not None
        and category["name"].strip().casefold() == OTHER_NAME.casefold()
    )


# --------------------------------------------------------------------------
# Helpers used by other db modules
# --------------------------------------------------------------------------

def insert_category(
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


def _other_id(conn: sqlite3.Connection, user_id: int, parent_id: int) -> int | None:
    """Id of a top-level category's 'Other' subcategory, if it exists."""
    row = conn.execute(
        "SELECT id FROM categories WHERE user_id = ? AND parent_id = ? AND name = ? COLLATE NOCASE",
        (user_id, parent_id, OTHER_NAME),
    ).fetchone()
    return row["id"] if row else None


def ensure_other(conn: sqlite3.Connection, user_id: int, parent_id: int, kind: str) -> int:
    """Find or create the 'Other' subcategory under a top-level category."""
    existing = _other_id(conn, user_id, parent_id)
    return existing if existing is not None else insert_category(conn, user_id, OTHER_NAME, parent_id, kind)


def resolve_category(conn: sqlite3.Connection, user_id: int, category_id: int) -> int:
    """Top-level categories become their 'Other' subcategory; subcategories pass through."""
    row = conn.execute(
        "SELECT id, parent_id, kind FROM categories WHERE id = ? AND user_id = ?",
        (category_id, user_id),
    ).fetchone()
    if row is None or row["parent_id"] is not None:
        return category_id  # unknown ids are rejected later by validation
    return ensure_other(conn, user_id, row["id"], row["kind"])


def category_clause(category_id: int | None) -> tuple[str, list[Any]]:
    """SQL fragment matching a category and its subcategories (aliases: c)."""
    if category_id is None:
        return "", []
    return " AND (c.id = ? OR c.parent_id = ?)", [category_id, category_id]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def get_category(user_id: int, category_id: int) -> dict[str, Any] | None:
    """One category (with its parent's name, if any)."""
    with connect() as conn:
        return one_row(conn.execute(
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
        return all_rows(conn.execute(
            f"SELECT c.id, c.name, c.parent_id, c.kind, p.name AS parent_name "
            f"FROM categories c LEFT JOIN categories p ON p.id = c.parent_id "
            f"WHERE c.user_id = ? "
            f"ORDER BY c.kind, COALESCE(p.sort_order, c.sort_order), "
            f"         CASE WHEN c.parent_id IS NULL THEN 0 ELSE 1 END, "
            f"         {_OTHER_LAST}, c.sort_order, c.name COLLATE NOCASE",
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
        return all_rows(conn.execute(
            "SELECT id, name, parent_id, kind FROM categories "
            "WHERE user_id = ? AND parent_id = ? "
            "ORDER BY CASE WHEN name = 'Other' COLLATE NOCASE THEN 1 ELSE 0 END, "
            "         sort_order, name COLLATE NOCASE",
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
        if top["id"] == exclude_id:
            continue
        options[top["id"]] = top["name"]
        for child in top["children"]:
            if child["id"] != exclude_id:
                options[child["id"]] = f"{top['name']} > {child['name']}"
    return options


# --------------------------------------------------------------------------
# Changing
# --------------------------------------------------------------------------

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
            return insert_category(conn, user_id, name, parent_id, kind)  # type: ignore[arg-type]
        except sqlite3.IntegrityError as exc:
            raise DataError(f"'{name}' already exists here.") from exc


def rename_category(user_id: int, category_id: int, new_name: str) -> None:
    """Rename a category or subcategory ('Other' is protected and reserved)."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise DataError("Please enter a name.")
    with connect() as conn:
        cat = conn.execute(
            "SELECT id, name, parent_id FROM categories WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        ).fetchone()
        if cat is None:
            raise DataError("Category not found.")
        if is_other(dict(cat)):
            raise DataError("'Other' is built in and can't be renamed.")
        if cat["parent_id"] is not None and new_name.casefold() == OTHER_NAME.casefold():
            raise DataError("'Other' is reserved for the built-in subcategory.")
        try:
            conn.execute(
                "UPDATE categories SET name = ? WHERE id = ? AND user_id = ?",
                (new_name, category_id, user_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DataError(f"'{new_name}' already exists here.") from exc


def move_category(user_id: int, category_id: int, direction: int) -> bool:
    """Move a category one step up (-1) or down (+1) among its siblings.

    'Other' can't be moved and nothing can move below it. Returns False if
    already first/last.
    """
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 (up) or 1 (down)")
    with connect() as conn:
        cat = conn.execute(
            "SELECT id, name, parent_id, kind FROM categories WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        ).fetchone()
        if cat is None:
            raise DataError("Category not found.")
        if is_other(dict(cat)):
            raise DataError("'Other' always stays at the bottom.")
        siblings = [dict(row) for row in conn.execute(
            "SELECT id, name, parent_id FROM categories "
            "WHERE user_id = ? AND COALESCE(parent_id, 0) = COALESCE(?, 0) AND kind = ? "
            "ORDER BY CASE WHEN parent_id IS NOT NULL AND name = 'Other' COLLATE NOCASE "
            "              THEN 1 ELSE 0 END, sort_order, name COLLATE NOCASE",
            (user_id, cat["parent_id"], cat["kind"]),
        )]
        ids = [s["id"] for s in siblings]
        index = ids.index(category_id)
        target = index + direction
        if not 0 <= target < len(ids) or is_other(siblings[target]):
            return False
        ids[index], ids[target] = ids[target], ids[index]
        conn.executemany(
            "UPDATE categories SET sort_order = ? WHERE id = ? AND user_id = ?",
            [(position, cid, user_id) for position, cid in enumerate(ids, start=1)],
        )
    return True


def _recurring_uses(conn: sqlite3.Connection, user_id: int, ids: list[int]) -> int:
    """Recurring rules and pending items filed under these categories."""
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT (SELECT COUNT(*) FROM recurring WHERE user_id = ? AND category_id IN ({marks})) "
        f"     + (SELECT COUNT(*) FROM pending_transactions WHERE user_id = ? AND category_id IN ({marks}))",
        [user_id, *ids, user_id, *ids],
    ).fetchone()[0]


def category_usage(user_id: int, category_id: int) -> dict[str, int]:
    """Transactions and (user-made) subcategories under a category.

    For a top-level category, its 'Other' subcategory is counted as part of the
    category itself: its transactions are included and it is not a subcategory.
    """
    with connect() as conn:
        other = _other_id(conn, user_id, category_id)
        ids = [category_id] + ([other] if other is not None else [])
        marks = ",".join("?" * len(ids))
        txns = conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE user_id = ? AND category_id IN ({marks})",
            [user_id, *ids],
        ).fetchone()[0]
        txns += _recurring_uses(conn, user_id, ids)
        subs = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE user_id = ? AND parent_id = ? AND id IS NOT ?",
            (user_id, category_id, other),
        ).fetchone()[0]
    return {"transactions": txns, "subcategories": subs}


def delete_category(user_id: int, category_id: int, reassign_to: int | None = None) -> None:
    """Delete a category (and its 'Other', if it has one).

    Blocked while it has subcategories other than 'Other'. If it (or its 'Other')
    has transactions, pass ``reassign_to`` (same kind) to move them first.
    'Other' itself can't be deleted on its own.
    """
    with connect() as conn:
        cat = conn.execute(
            "SELECT id, name, parent_id, kind FROM categories WHERE id = ? AND user_id = ?",
            (category_id, user_id),
        ).fetchone()
        if cat is None:
            raise DataError("Category not found.")
        if is_other(dict(cat)):
            raise DataError("'Other' is built in and can't be deleted.")
        other = _other_id(conn, user_id, category_id)
        ids = [category_id] + ([other] if other is not None else [])
        marks = ",".join("?" * len(ids))
        subs = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE user_id = ? AND parent_id = ? AND id IS NOT ?",
            (user_id, category_id, other),
        ).fetchone()[0]
        if subs:
            raise DataError(f"'{cat['name']}' still has subcategories. Delete those first.")
        txns = conn.execute(
            f"SELECT COUNT(*) FROM transactions WHERE user_id = ? AND category_id IN ({marks})",
            [user_id, *ids],
        ).fetchone()[0]
        txns += _recurring_uses(conn, user_id, ids)
        if txns:
            if reassign_to is None:
                raise DataError(
                    f"'{cat['name']}' has {txns} transaction(s). "
                    "Move them to another category before deleting it."
                )
            target_id = resolve_category(conn, user_id, reassign_to)
            target = conn.execute(
                "SELECT id, kind FROM categories WHERE id = ? AND user_id = ?",
                (target_id, user_id),
            ).fetchone()
            if target is None or target["id"] in ids:
                raise DataError("Choose a different category to move the transactions to.")
            if target["kind"] != cat["kind"]:
                raise DataError("Transactions can only move to a category of the same kind (income/expense).")
            conn.execute(
                f"UPDATE transactions SET category_id = ? WHERE user_id = ? AND category_id IN ({marks})",
                [target_id, user_id, *ids],
            )
            for table in ("recurring", "pending_transactions"):
                conn.execute(
                    f"UPDATE {table} SET category_id = ? WHERE user_id = ? AND category_id IN ({marks})",
                    [target_id, user_id, *ids],
                )
        if other is not None:
            conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", (other, user_id))
        conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", (category_id, user_id))
