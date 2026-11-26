"""Shared transactions, balances, settlements and the split maths.

A shared transaction is one expense paid and owed by two or more users. Each user
who owes something ends up with a normal personal transaction of -owed, linked
through shared_participants.transaction_id. The creator's copy appears at once;
everyone else gets a pending item first. Balances are never stored: they are
worked out from the active shared transactions plus the settlement entries.

User ids in these tables are deliberately NOT foreign keys: a user who is settled
up may delete their account, and the shared records stay with the other users
(the deleted user then shows as "Deleted user #<id>").

The "Split maths" section holds pure functions (no database): turning percentages
into cents and working out who owes whom. All money is integer minor units
(pence/cents). Rounding uses exact ``Fraction`` arithmetic, so results never depend
on floating-point error. Problems raise ``SplitError``.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timezone
from fractions import Fraction
from typing import Any

from db import categories
from db.core import DataError, all_rows, connect


# --------------------------------------------------------------------------
# Split maths (pure functions)
# --------------------------------------------------------------------------

PERCENT_TOLERANCE = Fraction(5, 100)  # percentages may sum to 100 +/- 0.05


class SplitError(ValueError):
    """The numbers given cannot be split (bad total, bad percentages, unbalanced nets)."""


def _largest_remainder(total: int, weights: list[Fraction]) -> list[int]:
    """Share ``total`` in proportion to ``weights`` so the parts add up exactly to ``total``.

    Each part is rounded down, then the leftover cents go one at a time to the parts with
    the largest fractional remainder. Ties go to the earliest part in the list.
    """
    weight_sum = sum(weights)
    exact = [Fraction(total) * w / weight_sum for w in weights]
    parts = [int(x) for x in exact]  # floor: every value here is >= 0
    leftover = total - sum(parts)
    order = sorted(range(len(parts)), key=lambda i: (-(exact[i] - parts[i]), i))
    for i in order[:leftover]:
        parts[i] += 1
    return parts


def percents_to_cents(total_cents: int, percents: Iterable[float | int | str]) -> list[int]:
    """Convert percentages into cents that add up exactly to ``total_cents``.

    ``percents`` must sum to 100 +/- 0.05. They are normalised by their actual sum, so
    33.33 / 33.33 / 33.33 is treated as thirds. Leftover cents go by largest remainder,
    ties to the first participant (so list the creator first). Returns one amount per
    percentage, in the same order.
    """
    try:
        weights = [Fraction(str(p).strip()) for p in percents]
    except (ValueError, ZeroDivisionError) as exc:
        raise SplitError("Percentages must be numbers.") from exc
    if not weights:
        raise SplitError("There is nobody to split between.")
    if not isinstance(total_cents, int) or isinstance(total_cents, bool) or total_cents <= 0:
        raise SplitError("The total must be greater than zero.")
    if any(w < 0 for w in weights):
        raise SplitError("Percentages cannot be negative.")
    if abs(sum(weights) - 100) > PERCENT_TOLERANCE:
        raise SplitError("The percentages must add up to 100.")
    return _largest_remainder(total_cents, weights)


def shares(total: int, weights: Iterable[float | int | str]) -> list[int]:
    """Share ``total`` in proportion to any non-negative ``weights`` (not just percentages).

    Same rounding as ``percents_to_cents``: the parts add up exactly to ``total``, leftover
    units go by largest remainder, ties to the first entry. The split dialog uses it to
    show cents as percentages (``shares(10000, cents)`` gives hundredths of a percent).
    """
    try:
        w = [Fraction(str(x).strip()) for x in weights]
    except (ValueError, ZeroDivisionError) as exc:
        raise SplitError("Weights must be numbers.") from exc
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise SplitError("The total must be zero or more.")
    if not w or any(x < 0 for x in w) or sum(w) == 0:
        raise SplitError("There is nothing to share between.")
    return _largest_remainder(total, w)


def pair_debts(nets: dict[int, int]) -> dict[tuple[int, int], int]:
    """Who owes whom for one shared transaction.

    ``nets`` maps user id -> paid minus owed (in cents) and must sum to zero. Returns
    ``{(debtor_id, creditor_id): cents}`` with only non-zero entries. Each debtor's
    shortfall is spread over the creditors in proportion to what they are owed, using
    largest-remainder rounding per debtor (ties to the lowest user id, so the result
    never depends on dict order).
    """
    if sum(nets.values()) != 0:
        raise SplitError("Paid and owed amounts do not balance.")
    creditors = sorted((uid for uid, n in nets.items() if n > 0))
    debtors = sorted((uid for uid, n in nets.items() if n < 0))
    weights = [Fraction(nets[c]) for c in creditors]

    debts: dict[tuple[int, int], int] = {}
    for debtor in debtors:
        parts = _largest_remainder(-nets[debtor], weights)
        for creditor, cents in zip(creditors, parts):
            if cents:
                debts[(debtor, creditor)] = cents
    return debts


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _now() -> str:
    """Current UTC time as an ISO string, for created_at columns."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _names(conn: sqlite3.Connection, ids: Any) -> dict[int, str]:
    """{user id: name}; users that no longer exist are called 'Deleted user #<id>'."""
    wanted = sorted({i for i in ids if i is not None})
    found: dict[int, str] = {}
    if wanted:
        marks = ",".join("?" * len(wanted))
        found = {r["id"]: r["name"]
                 for r in conn.execute(f"SELECT id, name FROM users WHERE id IN ({marks})", wanted)}
    return {i: found.get(i, f"Deleted user #{i}") for i in wanted}


def _expense_category(conn: sqlite3.Connection, user_id: int, category_id: int | None) -> int:
    """Check ``category_id`` is one of the user's expense categories; return the id to file under
    (a top-level category becomes its 'Other' subcategory)."""
    row = None
    if category_id is not None:
        row = conn.execute(
            "SELECT kind FROM categories WHERE id = ? AND user_id = ?", (category_id, user_id)
        ).fetchone()
    if row is None:
        raise DataError("Please choose a category.")
    if row["kind"] != "expense":
        raise DataError("Shared transactions need an expense category.")
    return categories.resolve_category(conn, user_id, category_id)  # type: ignore[arg-type]


def _insert_personal(
    conn: sqlite3.Connection, user_id: int, iso_date: str, cents: int, category_id: int, description: str
) -> int:
    """Insert a participant's personal copy (already validated) and return its id."""
    cur = conn.execute(
        "INSERT INTO transactions(user_id, date, amount_cents, category_id, description) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, iso_date, cents, category_id, description),
    )
    return int(cur.lastrowid)


def _remove_personal_copies(conn: sqlite3.Connection, shared_id: int) -> None:
    """Delete every personal transaction linked to a shared transaction.

    Order matters (foreign keys): unlink the participant rows first, then delete.
    """
    ids = [r["transaction_id"] for r in conn.execute(
        "SELECT transaction_id FROM shared_participants "
        "WHERE shared_id = ? AND transaction_id IS NOT NULL", (shared_id,))]
    conn.execute("UPDATE shared_participants SET transaction_id = NULL WHERE shared_id = ?", (shared_id,))
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM transactions WHERE id IN ({marks})", ids)


# --------------------------------------------------------------------------
# Creating, accepting, rejecting, deleting
# --------------------------------------------------------------------------

def create_shared_transaction(
    creator_id: int, tx_date: str, description: str, total_cents: int,
    category_id: int | None, participants: list[dict[str, int]],
) -> int:
    """Create a shared expense and return its id.

    ``participants`` is a list of ``{'user_id', 'paid_cents', 'owed_cents'}`` and must
    include the creator. Paid and owed must each add up to ``total_cents``. ``category_id``
    (an expense category of the creator) is only needed if the creator owes something.
    The creator's personal copy is added now; others get a pending item, except people
    who owe nothing, who are marked accepted straight away.
    """
    try:
        iso = date.fromisoformat(tx_date).isoformat()
    except (TypeError, ValueError) as exc:
        raise DataError("Please enter a valid date (YYYY-MM-DD).") from exc
    if not _is_int(total_cents) or total_cents <= 0:
        raise DataError("The total must be greater than zero.")

    rows: list[tuple[int, int, int]] = []
    for p in participants or []:
        try:
            uid, paid, owed = p["user_id"], p["paid_cents"], p["owed_cents"]
        except (KeyError, TypeError) as exc:
            raise DataError("Each person needs a user, an amount paid and an amount owed.") from exc
        if not (_is_int(uid) and _is_int(paid) and _is_int(owed)) or paid < 0 or owed < 0:
            raise DataError("Paid and owed amounts must be zero or more.")
        if paid + owed == 0:
            raise DataError("Everyone in a split must have paid or owe something. Remove anyone who doesn't.")
        rows.append((uid, paid, owed))
    ids = [r[0] for r in rows]
    if len(set(ids)) != len(ids):
        raise DataError("Each person can only appear once in a split.")
    if len(rows) < 2:
        raise DataError("A split needs at least two people.")
    if creator_id not in ids:
        raise DataError("You must be part of a split you create.")
    if sum(r[1] for r in rows) != total_cents:
        raise DataError("The amounts paid must add up to the total.")
    if sum(r[2] for r in rows) != total_cents:
        raise DataError("The amounts owed must add up to the total.")
    rows.sort(key=lambda r: (r[0] != creator_id, r[0]))  # creator first
    text = (description or "").strip()

    with connect() as conn:
        marks = ",".join("?" * len(ids))
        known = {r["id"] for r in conn.execute(f"SELECT id FROM users WHERE id IN ({marks})", ids)}
        if known != set(ids):
            raise DataError("One of the chosen users no longer exists.")
        creator_owed = rows[0][2]
        personal_category = _expense_category(conn, creator_id, category_id) if creator_owed > 0 else None
        cur = conn.execute(
            "INSERT INTO shared_transactions(creator_id, date, description, total_cents, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (creator_id, iso, text, total_cents, _now()),
        )
        shared_id = int(cur.lastrowid)
        for uid, paid, owed in rows:
            transaction_id = None
            if uid == creator_id and owed > 0:
                transaction_id = _insert_personal(conn, uid, iso, -owed, personal_category, text)  # type: ignore[arg-type]
            status = "pending" if (uid != creator_id and owed > 0) else "accepted"
            conn.execute(
                "INSERT INTO shared_participants(shared_id, user_id, paid_cents, owed_cents, status, transaction_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (shared_id, uid, paid, owed, status, transaction_id),
            )
    return shared_id


def list_shared_pending(user_id: int) -> list[dict[str, Any]]:
    """Shared transactions waiting for this user to accept or reject, oldest first."""
    with connect() as conn:
        items = all_rows(conn.execute(
            "SELECT s.id, s.date, s.description, s.creator_id, s.total_cents, p.paid_cents, p.owed_cents "
            "FROM shared_participants p JOIN shared_transactions s ON s.id = p.shared_id "
            "WHERE p.user_id = ? AND p.status = 'pending' AND s.status = 'active' "
            "ORDER BY s.date, s.id",
            (user_id,),
        ))
        names = _names(conn, (i["creator_id"] for i in items))
    for item in items:
        item["creator_name"] = names[item["creator_id"]]
    return items


def accept_shared(user_id: int, shared_id: int, category_id: int) -> None:
    """Accept a pending share: file it under an expense category as a personal transaction."""
    with connect() as conn:
        row = conn.execute(
            "SELECT p.status, p.owed_cents, s.date, s.description, s.status AS shared_status "
            "FROM shared_participants p JOIN shared_transactions s ON s.id = p.shared_id "
            "WHERE p.shared_id = ? AND p.user_id = ?",
            (shared_id, user_id),
        ).fetchone()
        if row is None:
            raise DataError("Shared transaction not found.")
        if row["shared_status"] != "active":
            raise DataError("This split has been cancelled.")
        if row["status"] != "pending":
            raise DataError("You have already accepted this one.")
        category = _expense_category(conn, user_id, category_id)
        transaction_id = _insert_personal(
            conn, user_id, row["date"], -row["owed_cents"], category, row["description"])
        conn.execute(
            "UPDATE shared_participants SET status = 'accepted', transaction_id = ? "
            "WHERE shared_id = ? AND user_id = ?",
            (transaction_id, shared_id, user_id),
        )


def reject_shared(user_id: int, shared_id: int) -> None:
    """Reject a pending share. Cancels the split for everyone, removes every personal copy
    and leaves a 'Rejected' record. Only possible while this user's share is pending."""
    with connect() as conn:
        row = conn.execute(
            "SELECT p.status, s.status AS shared_status "
            "FROM shared_participants p JOIN shared_transactions s ON s.id = p.shared_id "
            "WHERE p.shared_id = ? AND p.user_id = ?",
            (shared_id, user_id),
        ).fetchone()
        if row is None:
            raise DataError("Shared transaction not found.")
        if row["shared_status"] != "active":
            raise DataError("This split has already been cancelled.")
        if row["status"] != "pending":
            raise DataError("You can only reject a share that is still pending. Delete it instead.")
        _remove_personal_copies(conn, shared_id)
        conn.execute(
            "UPDATE shared_transactions SET status = 'rejected', rejected_by = ? WHERE id = ?",
            (user_id, shared_id),
        )


def delete_shared(user_id: int, shared_id: int) -> None:
    """Delete a shared transaction for everyone, leaving no record.

    Allowed for the creator at any time and for participants who have accepted.
    Anyone in a split can clear a 'Rejected' record. Pending participants use reject.
    """
    with connect() as conn:
        head = conn.execute(
            "SELECT creator_id, status FROM shared_transactions WHERE id = ?", (shared_id,)
        ).fetchone()
        mine = conn.execute(
            "SELECT status FROM shared_participants WHERE shared_id = ? AND user_id = ?",
            (shared_id, user_id),
        ).fetchone()
        if head is None or mine is None:
            raise DataError("Shared transaction not found.")
        if head["status"] == "active" and mine["status"] == "pending":
            raise DataError("You haven't accepted this yet. Use Reject to cancel it.")
        _remove_personal_copies(conn, shared_id)
        conn.execute("DELETE FROM shared_participants WHERE shared_id = ?", (shared_id,))
        conn.execute("DELETE FROM shared_transactions WHERE id = ?", (shared_id,))


def list_shared(user_id: int) -> list[dict[str, Any]]:
    """Every shared transaction this user is in (rejected ones too), newest first.

    Each item has the header fields, ``participants`` (creator first, with names),
    ``my_paid``, ``my_owed``, ``my_status`` ('pending', 'accepted' or 'rejected') and
    ``can_delete``.
    """
    with connect() as conn:
        heads = all_rows(conn.execute(
            "SELECT s.id, s.creator_id, s.date, s.description, s.total_cents, s.status, "
            "       s.rejected_by, s.created_at "
            "FROM shared_transactions s WHERE EXISTS ("
            "  SELECT 1 FROM shared_participants m WHERE m.shared_id = s.id AND m.user_id = ?) "
            "ORDER BY s.date DESC, s.id DESC",
            (user_id,),
        ))
        if not heads:
            return []
        marks = ",".join("?" * len(heads))
        parts = all_rows(conn.execute(
            f"SELECT shared_id, user_id, paid_cents, owed_cents, status "
            f"FROM shared_participants WHERE shared_id IN ({marks})",
            [h["id"] for h in heads],
        ))
        names = _names(conn, [p["user_id"] for p in parts]
                       + [h["creator_id"] for h in heads] + [h["rejected_by"] for h in heads])
    by_shared: dict[int, list[dict[str, Any]]] = {}
    for p in parts:
        p["name"] = names[p["user_id"]]
        by_shared.setdefault(p["shared_id"], []).append(p)
    for head in heads:
        people = sorted(by_shared[head["id"]], key=lambda p: (p["user_id"] != head["creator_id"], p["user_id"]))
        mine = next(p for p in people if p["user_id"] == user_id)
        head["participants"] = people
        head["creator_name"] = names[head["creator_id"]]
        head["rejected_by_name"] = names.get(head["rejected_by"]) if head["rejected_by"] else None
        head["my_paid"], head["my_owed"] = mine["paid_cents"], mine["owed_cents"]
        head["my_status"] = "rejected" if head["status"] == "rejected" else mine["status"]
        head["can_delete"] = head["status"] == "rejected" or mine["status"] == "accepted"
    return heads


# --------------------------------------------------------------------------
# Balances and settlements
# --------------------------------------------------------------------------

def _balances(conn: sqlite3.Connection, user_id: int) -> dict[int, int]:
    """{other user id: signed cents}; positive means they owe ``user_id``.

    Running total of the shares of active shared transactions (pending included,
    rejected excluded) plus settlement payments. Every user the person has dealt
    with appears, even at 0.
    """
    balances: dict[int, int] = {}
    nets: dict[int, dict[int, int]] = {}
    for r in conn.execute(
        "SELECT p.shared_id, p.user_id, p.paid_cents, p.owed_cents FROM shared_participants p "
        "JOIN shared_transactions s ON s.id = p.shared_id AND s.status = 'active' "
        "WHERE p.shared_id IN (SELECT shared_id FROM shared_participants WHERE user_id = ?)",
        (user_id,),
    ):
        nets.setdefault(r["shared_id"], {})[r["user_id"]] = r["paid_cents"] - r["owed_cents"]
    for people in nets.values():
        for uid in people:
            if uid != user_id:
                balances.setdefault(uid, 0)
        try:
            debts = pair_debts(people)
        except SplitError as exc:
            raise DataError("A shared transaction in the database does not add up.") from exc
        for (debtor, creditor), cents in debts.items():
            if creditor == user_id:
                balances[debtor] += cents
            elif debtor == user_id:
                balances[creditor] -= cents
    for r in conn.execute(
        "SELECT from_user_id, to_user_id, amount_cents FROM settlements "
        "WHERE from_user_id = ? OR to_user_id = ?",
        (user_id, user_id),
    ):
        if r["to_user_id"] == user_id:  # they paid me, so they owe me less
            balances[r["from_user_id"]] = balances.get(r["from_user_id"], 0) - r["amount_cents"]
        else:                           # I paid them, so I owe them less
            balances[r["to_user_id"]] = balances.get(r["to_user_id"], 0) + r["amount_cents"]
    return balances


def shared_balances(user_id: int) -> list[dict[str, Any]]:
    """One signed net per other user, by name: ``{'user_id', 'name', 'cents'}``.

    Positive means they owe you, negative means you owe them. Includes 0 balances.
    """
    with connect() as conn:
        balances = _balances(conn, user_id)
        names = _names(conn, balances)
    items = [{"user_id": uid, "name": names[uid], "cents": cents} for uid, cents in balances.items()]
    return sorted(items, key=lambda i: i["name"].casefold())


def settle_balance(user_id: int, other_id: int, expected_cents: int) -> int:
    """Zero the balance between two users by recording a settlement; return its id.

    ``expected_cents`` is the balance the user saw (as in ``shared_balances``). If it
    has moved since, nothing is recorded.
    """
    if user_id == other_id:
        raise DataError("You can't settle with yourself.")
    with connect() as conn:
        balance = _balances(conn, user_id).get(other_id, 0)
        if balance != expected_cents:
            raise DataError("The balance changed since you opened this. Check the new amount and try again.")
        if balance == 0:
            raise DataError("There is nothing to settle.")
        payer, receiver = (other_id, user_id) if balance > 0 else (user_id, other_id)
        cur = conn.execute(
            "INSERT INTO settlements(from_user_id, to_user_id, amount_cents, settled_by, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (payer, receiver, abs(balance), user_id, _now()),
        )
        return int(cur.lastrowid)


def delete_settlement(user_id: int, settlement_id: int) -> None:
    """Delete a settlement entry (either party). The previous balance comes back."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM settlements WHERE id = ? AND (from_user_id = ? OR to_user_id = ?)",
            (settlement_id, user_id, user_id),
        )
        if cur.rowcount == 0:
            raise DataError("Settlement not found.")


def list_settlements(user_id: int) -> list[dict[str, Any]]:
    """Settlements this user is part of, newest first, with names."""
    with connect() as conn:
        items = all_rows(conn.execute(
            "SELECT id, from_user_id, to_user_id, amount_cents, settled_by, created_at "
            "FROM settlements WHERE from_user_id = ? OR to_user_id = ? ORDER BY created_at DESC, id DESC",
            (user_id, user_id),
        ))
        names = _names(conn, [i[k] for i in items for k in ("from_user_id", "to_user_id", "settled_by")])
    for item in items:
        item["from_name"] = names[item["from_user_id"]]
        item["to_name"] = names[item["to_user_id"]]
        item["settled_by_name"] = names[item["settled_by"]]
    return items


def ensure_can_leave(conn: sqlite3.Connection, user_id: int) -> None:
    """Used by ``users.delete_user``: refuse while the user has unsettled shared money or open shares."""
    owing = {uid: c for uid, c in _balances(conn, user_id).items() if c != 0}
    if owing:
        names = _names(conn, owing)
        raise DataError(
            "You can't delete your account while shared balances are unsettled. "
            "Settle up with: " + ", ".join(names[uid] for uid in owing) + "."
        )
    if conn.execute(
        "SELECT 1 FROM shared_participants p JOIN shared_transactions s ON s.id = p.shared_id "
        "WHERE p.user_id = ? AND p.status = 'pending' AND s.status = 'active'", (user_id,)
    ).fetchone():
        raise DataError("Accept or reject your pending shared transactions before deleting your account.")