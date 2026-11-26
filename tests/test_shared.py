"""Tests for shared transactions (db layer + split maths).

Run from anywhere in the project:   pytest
Each test gets its own throw-away database (see conftest.py), so lantern.db is never touched.
"""
from __future__ import annotations

import re

import pytest

from db import DataError, core, shared_transactions, transactions, users

TODAY = "2026-11-24"
Trio = tuple[int, int, int]


def user(name: str) -> int:
    return users.add_user(name, "pass1234")


def cat(user_id: int, label: str) -> int:
    """Category id by name (top-level or sub), e.g. 'Groceries' or 'Salary'."""
    with core.connect() as conn:
        return conn.execute(
            "SELECT id FROM categories WHERE user_id = ? AND name = ? AND parent_id IS NOT NULL",
            (user_id, label),
        ).fetchone()["id"]


def split(creator: int, total: int, rows: list[tuple[int, int, int]], category: int | None) -> int:
    return shared_transactions.create_shared_transaction(
        creator, TODAY, "Dinner", total, category,
        [{"user_id": u, "paid_cents": p, "owed_cents": o} for u, p, o in rows],
    )


def balance(me: int, other: int) -> int:
    return next((b["cents"] for b in shared_transactions.shared_balances(me) if b["user_id"] == other), 0)


def raises(fn, *args, contains: str = "") -> None:
    """Call ``fn`` and expect a DataError whose message contains ``contains`` (any case)."""
    with pytest.raises(DataError, match=f"(?i){re.escape(contains)}"):
        fn(*args)


def user_exists(user_id: int) -> bool:
    with core.connect() as conn:
        return conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None


def test_two_users_even_split(abc: Trio) -> None:
    a, b, _ = abc
    sid = split(a, 5000, [(a, 5000, 2500), (b, 0, 2500)], cat(a, "Groceries"))
    mine = [t for t in transactions.list_transactions(a) if t["shared_id"] == sid]
    assert [t["amount_cents"] for t in mine] == [-2500]
    assert [p["id"] for p in shared_transactions.list_shared_pending(b)] == [sid]
    assert balance(a, b) == 2500 and balance(b, a) == -2500      # pending counts
    shared_transactions.accept_shared(b, sid, cat(b, "Groceries"))
    assert shared_transactions.list_shared_pending(b) == []
    assert [t["amount_cents"] for t in transactions.list_transactions(b)] == [-2500]
    assert balance(a, b) == 2500


def test_three_users_thirds(abc: Trio) -> None:
    a, b, c = abc
    cents = shared_transactions.percents_to_cents(10000, [33.33, 33.33, 33.33])
    assert cents == [3334, 3333, 3333]
    split(a, 10000, [(a, 10000, cents[0]), (b, 0, cents[1]), (c, 0, cents[2])], cat(a, "Eating out"))
    assert balance(a, b) == 3333 and balance(a, c) == 3333 and balance(b, c) == 0
    assert [t["amount_cents"] for t in transactions.list_transactions(a)] == [-3334]
    assert len(shared_transactions.list_shared_pending(b)) == len(shared_transactions.list_shared_pending(c)) == 1
    assert shared_transactions.list_shared(a)[0]["participants"][0]["user_id"] == a  # creator first


def test_payer_who_owes_nothing(abc: Trio) -> None:
    a, b, _ = abc
    sid = split(a, 5000, [(a, 5000, 0), (b, 0, 5000)], None)     # no category needed
    assert transactions.list_transactions(a) == []
    assert shared_transactions.list_shared_pending(a) == []
    me = next(p for p in shared_transactions.list_shared(a)[0]["participants"] if p["user_id"] == a)
    assert me["status"] == "accepted"
    assert shared_transactions.list_shared(a)[0]["my_status"] == "accepted"
    assert balance(a, b) == 5000
    shared_transactions.delete_shared(a, sid)                    # creator can delete


def test_reject_while_pending(abc: Trio) -> None:
    a, b, c = abc
    sid = split(a, 6000, [(a, 6000, 2000), (b, 0, 2000), (c, 0, 2000)], cat(a, "Groceries"))
    shared_transactions.accept_shared(c, sid, cat(c, "Groceries"))   # Cara accepted, Bob rejects
    shared_transactions.reject_shared(b, sid)
    for u in (a, b, c):
        assert transactions.list_transactions(u) == []
        assert shared_transactions.list_shared_pending(u) == []
    assert balance(a, b) == 0 and balance(a, c) == 0
    rec = shared_transactions.list_shared(a)[0]
    assert rec["status"] == "rejected" and rec["rejected_by_name"] == "Bob"
    assert rec["my_status"] == "rejected"
    raises(shared_transactions.reject_shared, b, sid, contains="cancelled")
    raises(shared_transactions.accept_shared, c, sid, cat(c, "Groceries"), contains="cancelled")
    shared_transactions.delete_shared(c, sid)                    # any participant clears the record
    assert shared_transactions.list_shared(a) == [] and shared_transactions.list_shared(b) == []


def test_reject_needs_pending(abc: Trio) -> None:
    a, b, _ = abc
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    raises(shared_transactions.reject_shared, a, sid, contains="pending")   # creator is never pending
    shared_transactions.accept_shared(b, sid, cat(b, "Groceries"))
    raises(shared_transactions.reject_shared, b, sid, contains="pending")
    raises(shared_transactions.accept_shared, b, sid, cat(b, "Groceries"), contains="already")


def test_delete_after_acceptance(abc: Trio) -> None:
    a, b, _ = abc
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    raises(shared_transactions.delete_shared, b, sid, contains="Reject")    # pending participants must reject
    shared_transactions.accept_shared(b, sid, cat(b, "Groceries"))
    shared_transactions.delete_shared(b, sid)                    # accepted participant deletes for all
    for u in (a, b):
        assert shared_transactions.list_shared(u) == [] and transactions.list_transactions(u) == []
    assert balance(a, b) == 0
    raises(shared_transactions.delete_shared, a, sid, contains="not found")


def test_settle_then_delete_transaction_flips_balance(abc: Trio) -> None:
    a, b, _ = abc
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    raises(shared_transactions.settle_balance, b, a, -1500, contains="changed")
    shared_transactions.settle_balance(b, a, -2000)              # Bob's view: owes Alice 20.00
    assert balance(a, b) == 0 and balance(b, a) == 0
    raises(shared_transactions.settle_balance, a, b, 0, contains="nothing")
    shared_transactions.delete_shared(a, sid)
    assert balance(a, b) == -2000 and balance(b, a) == 2000      # flip: Alice now owes Bob 20.00


def test_delete_settlement_restores_balance(abc: Trio) -> None:
    a, b, _ = abc
    split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    settlement = shared_transactions.settle_balance(a, b, 2000)  # either party can settle
    entry = shared_transactions.list_settlements(b)[0]
    assert (entry["from_user_id"], entry["to_user_id"], entry["amount_cents"]) == (b, a, 2000)
    assert entry["settled_by"] == a
    raises(shared_transactions.delete_settlement, user("Outsider"), settlement, contains="not found")
    shared_transactions.delete_settlement(b, settlement)         # either party can delete it
    assert balance(a, b) == 2000 and shared_transactions.list_settlements(a) == []


def test_account_deletion(abc: Trio) -> None:
    a, b, _ = abc
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    raises(users.delete_user, b, contains="unsettled")           # owes money
    raises(users.delete_user, a, contains="unsettled")           # is owed money
    shared_transactions.settle_balance(a, b, 2000)
    shared_transactions.accept_shared(b, sid, cat(b, "Groceries"))
    users.delete_user(b)                                         # settled: allowed
    assert not user_exists(b)
    rec = shared_transactions.list_shared(a)[0]                  # the record stays with Alice
    assert [p["name"] for p in rec["participants"]][1] == f"Deleted user #{b}"
    assert [t["amount_cents"] for t in transactions.list_transactions(a)] == [-2000]
    newcomer = user("Newcomer")                                  # must not inherit Bob's id
    assert newcomer != b and balance(newcomer, a) == 0
    # Alice deletes the shared record: balance with the deleted user now shows, can be settled
    shared_transactions.delete_shared(a, sid)
    assert balance(a, b) == -2000
    shared_transactions.settle_balance(a, b, -2000)
    assert balance(a, b) == 0


def test_pending_blocks_account_deletion(abc: Trio) -> None:
    a, b, _ = abc
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    shared_transactions.settle_balance(a, b, 2000)               # balance 0 but Bob still has a pending item
    raises(users.delete_user, b, contains="pending")
    shared_transactions.reject_shared(b, sid)


def test_personal_copies_are_locked(abc: Trio) -> None:
    a, b, _ = abc
    split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    tid = transactions.list_transactions(a)[0]["id"]
    raises(transactions.delete_transaction, a, tid, contains="shared")
    raises(transactions.update_transaction, a, tid, TODAY, -1000, cat(a, "Groceries"), "x", contains="shared")
    plain = transactions.add_transaction(a, TODAY, -500, cat(a, "Groceries"), "coffee")
    transactions.delete_transaction(a, plain)                    # ordinary ones still work


def test_validation(abc: Trio) -> None:
    a, b, c = abc
    g = cat(a, "Groceries")
    raises(split, a, 5000, [(a, 5000, 5000)], g, contains="at least two")
    raises(split, a, 5000, [(b, 5000, 2500), (c, 0, 2500)], g, contains="part of")
    raises(split, a, 5000, [(a, 4000, 2500), (b, 0, 2500)], g, contains="paid")
    raises(split, a, 5000, [(a, 5000, 2000), (b, 0, 2500)], g, contains="owed")
    raises(split, a, 5000, [(a, 5000, 2500), (b, 0, 2500)], None, contains="category")
    raises(split, a, 5000, [(a, 5000, 2500), (b, 0, 2500)], cat(a, "Salary"), contains="expense")
    raises(split, a, 5000, [(a, 5000, 2500), (b, 0, 2500), (c, 0, 0)], g, contains="paid or owe")
    raises(split, a, 5000, [(a, 5000, 2500), (a, 0, 2500)], g, contains="once")
    raises(split, a, 5000, [(a, 5000, 2500), (99999, 0, 2500)], g, contains="no longer exists")
    raises(split, a, 0, [(a, 0, 0), (b, 0, 0)], g, contains="total")
    sid = split(a, 5000, [(a, 5000, 2500), (b, 0, 2500)], g)
    raises(shared_transactions.accept_shared, b, sid, cat(b, "Salary"), contains="expense")
    raises(shared_transactions.accept_shared, a, sid, g, contains="already")
    raises(shared_transactions.accept_shared, c, sid, cat(c, "Groceries"), contains="not found")
    raises(shared_transactions.delete_shared, c, sid, contains="not found")  # outsiders can't touch it
    raises(shared_transactions.settle_balance, a, a, 0, contains="yourself")
    assert shared_transactions.list_shared(c) == []
    # a top-level category is accepted and filed under its 'Other' subcategory
    with core.connect() as conn:
        top = conn.execute("SELECT id FROM categories WHERE user_id = ? AND name = 'Food' "
                           "AND parent_id IS NULL", (b,)).fetchone()["id"]
    shared_transactions.accept_shared(b, sid, top)
    assert transactions.list_transactions(b)[0]["category_name"] == "Other"


def test_uneven_multi_payer(abc: Trio) -> None:
    a, b, c = abc
    # Alice pays 60, Bob pays 40; owed 50 / 25 / 25
    split(a, 10000, [(a, 6000, 5000), (b, 4000, 2500), (c, 0, 2500)], cat(a, "Groceries"))
    assert balance(a, c) == 1000 and balance(b, c) == 1500 and balance(a, b) == 0
    assert balance(c, a) == -1000 and balance(c, b) == -1500
