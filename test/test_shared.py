"""Tests for shared transactions (db layer + splits maths).

Run from the project folder:   python test_shared.py
Uses a throw-away database, so your finance.db is never touched.
"""
from __future__ import annotations

import itertools
import os
import tempfile

os.environ["FINANCE_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import db  # noqa: E402
import splits  # noqa: E402

TODAY = "2026-11-24"
db.init_db()


def user(name: str) -> int:
    return db.add_user(name, "pass1234")


def cat(user_id: int, label: str) -> int:
    """Category id by name (top-level or sub), e.g. 'Groceries' or 'Salary'."""
    with db.connect() as conn:
        return conn.execute(
            "SELECT id FROM categories WHERE user_id = ? AND name = ? AND parent_id IS NOT NULL",
            (user_id, label),
        ).fetchone()["id"]


def split(creator: int, total: int, rows: list[tuple[int, int, int]], category: int | None) -> int:
    return db.create_shared_transaction(
        creator, TODAY, "Dinner", total, category,
        [{"user_id": u, "paid_cents": p, "owed_cents": o} for u, p, o in rows],
    )


def balance(me: int, other: int) -> int:
    return next((b["cents"] for b in db.shared_balances(me) if b["user_id"] == other), 0)


def raises(fn, *args, contains: str = "") -> None:
    try:
        fn(*args)
    except db.DataError as exc:
        assert contains.lower() in str(exc).lower(), f"wrong message: {exc}"
        return
    raise AssertionError(f"expected DataError containing {contains!r}")


_counter = itertools.count(1)


def fresh() -> tuple[int, int, int]:
    """Three brand-new users, so each test starts from a clean slate."""
    n = next(_counter)
    return user(f"A{n}"), user(f"B{n}"), user(f"C{n}")


def user_exists(user_id: int) -> bool:
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None


def test_two_users_even_split() -> None:
    a, b, _ = fresh()
    sid = split(a, 5000, [(a, 5000, 2500), (b, 0, 2500)], cat(a, "Groceries"))
    mine = [t for t in db.list_transactions(a) if t["shared_id"] == sid]
    assert [t["amount_cents"] for t in mine] == [-2500]
    assert [p["id"] for p in db.list_shared_pending(b)] == [sid]
    assert balance(a, b) == 2500 and balance(b, a) == -2500      # pending counts
    db.accept_shared(b, sid, cat(b, "Groceries"))
    assert db.list_shared_pending(b) == []
    assert [t["amount_cents"] for t in db.list_transactions(b)] == [-2500]
    assert balance(a, b) == 2500


def test_three_users_thirds() -> None:
    a, b, c = fresh()
    cents = splits.percents_to_cents(10000, [33.33, 33.33, 33.33])
    assert cents == [3334, 3333, 3333]
    sid = split(a, 10000, [(a, 10000, cents[0]), (b, 0, cents[1]), (c, 0, cents[2])], cat(a, "Eating out"))
    assert balance(a, b) == 3333 and balance(a, c) == 3333 and balance(b, c) == 0
    assert [t["amount_cents"] for t in db.list_transactions(a)] == [-3334]
    assert len(db.list_shared_pending(b)) == len(db.list_shared_pending(c)) == 1
    assert db.list_shared(a)[0]["participants"][0]["user_id"] == a  # creator first


def test_payer_who_owes_nothing() -> None:
    a, b, _ = fresh()
    sid = split(a, 5000, [(a, 5000, 0), (b, 0, 5000)], None)     # no category needed
    assert db.list_transactions(a) == []
    assert db.list_shared_pending(a) == []
    me = next(p for p in db.list_shared(a)[0]["participants"] if p["user_id"] == a)
    assert me["status"] == "accepted"
    assert db.list_shared(a)[0]["my_status"] == "accepted"
    assert balance(a, b) == 5000
    db.delete_shared(a, sid)                                     # creator can delete


def test_reject_while_pending() -> None:
    a, b, c = fresh()
    sid = split(a, 6000, [(a, 6000, 2000), (b, 0, 2000), (c, 0, 2000)], cat(a, "Groceries"))
    db.accept_shared(c, sid, cat(c, "Groceries"))                # C accepted, B rejects
    db.reject_shared(b, sid)
    for u in (a, b, c):
        assert db.list_transactions(u) == []
        assert db.list_shared_pending(u) == []
        assert balance(a, b) == 0 and balance(a, c) == 0
    rec = db.list_shared(a)[0]
    assert rec["status"] == "rejected" and rec["rejected_by_name"].startswith("B")
    assert rec["my_status"] == "rejected"
    raises(db.reject_shared, b, sid, contains="cancelled")
    raises(db.accept_shared, c, sid, cat(c, "Groceries"), contains="cancelled")
    db.delete_shared(c, sid)                                     # any participant clears the record
    assert db.list_shared(a) == [] and db.list_shared(b) == []


def test_reject_needs_pending() -> None:
    a, b, _ = fresh()
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    raises(db.reject_shared, a, sid, contains="pending")          # creator is never pending
    db.accept_shared(b, sid, cat(b, "Groceries"))
    raises(db.reject_shared, b, sid, contains="pending")
    raises(db.accept_shared, b, sid, cat(b, "Groceries"), contains="already")


def test_delete_after_acceptance() -> None:
    a, b, _ = fresh()
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    raises(db.delete_shared, b, sid, contains="Reject")          # pending participants must reject
    db.accept_shared(b, sid, cat(b, "Groceries"))
    db.delete_shared(b, sid)                                     # accepted participant deletes for all
    for u in (a, b):
        assert db.list_shared(u) == [] and db.list_transactions(u) == []
    assert balance(a, b) == 0
    raises(db.delete_shared, a, sid, contains="not found")


def test_settle_then_delete_transaction_flips_balance() -> None:
    a, b, _ = fresh()
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    raises(db.settle_balance, b, a, -1500, contains="changed")
    db.settle_balance(b, a, -2000)                               # B's view: owes A 20.00
    assert balance(a, b) == 0 and balance(b, a) == 0
    raises(db.settle_balance, a, b, 0, contains="nothing")
    db.delete_shared(a, sid)
    assert balance(a, b) == -2000 and balance(b, a) == 2000      # flip: A now owes B 20.00


def test_delete_settlement_restores_balance() -> None:
    a, b, _ = fresh()
    split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    settlement = db.settle_balance(a, b, 2000)                   # either party can settle
    entry = db.list_settlements(b)[0]
    assert (entry["from_user_id"], entry["to_user_id"], entry["amount_cents"]) == (b, a, 2000)
    assert entry["settled_by"] == a
    raises(db.delete_settlement, user(f"Outsider{next(_counter)}"), settlement, contains="not found")
    db.delete_settlement(b, settlement)                          # either party can delete it
    assert balance(a, b) == 2000 and db.list_settlements(a) == []


def test_account_deletion() -> None:
    a, b, _ = fresh()
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    raises(db.delete_user, b, contains="unsettled")              # owes money
    raises(db.delete_user, a, contains="unsettled")              # is owed money
    db.settle_balance(a, b, 2000)
    db.accept_shared(b, sid, cat(b, "Groceries"))
    db.delete_user(b)                                            # settled: allowed
    assert not user_exists(b)
    rec = db.list_shared(a)[0]                                   # the record stays with A
    assert [p["name"] for p in rec["participants"]][1] == f"Deleted user #{b}"
    assert [t["amount_cents"] for t in db.list_transactions(a)] == [-2000]
    newcomer = user(f"Newcomer{next(_counter)}")                                  # must not inherit B's id
    assert newcomer != b and balance(newcomer, a) == 0
    # A deletes the shared record: balance with the deleted user now shows, can be settled
    db.delete_shared(a, sid)
    assert balance(a, b) == -2000
    db.settle_balance(a, b, -2000)
    assert balance(a, b) == 0


def test_pending_blocks_account_deletion() -> None:
    a, b, _ = fresh()
    sid = split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    db.settle_balance(a, b, 2000)                                # balance 0 but B still has a pending item
    raises(db.delete_user, b, contains="pending")
    db.reject_shared(b, sid)


def test_personal_copies_are_locked() -> None:
    a, b, _ = fresh()
    split(a, 4000, [(a, 4000, 2000), (b, 0, 2000)], cat(a, "Groceries"))
    tid = db.list_transactions(a)[0]["id"]
    raises(db.delete_transaction, a, tid, contains="shared")
    raises(db.update_transaction, a, tid, TODAY, -1000, cat(a, "Groceries"), "x", contains="shared")
    plain = db.add_transaction(a, TODAY, -500, cat(a, "Groceries"), "coffee")
    db.delete_transaction(a, plain)                              # ordinary ones still work


def test_validation() -> None:
    a, b, c = fresh()
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
    raises(db.accept_shared, b, sid, cat(b, "Salary"), contains="expense")
    raises(db.accept_shared, a, sid, g, contains="already")
    raises(db.accept_shared, c, sid, cat(c, "Groceries"), contains="not found")
    raises(db.delete_shared, c, sid, contains="not found")       # outsiders can't touch it
    raises(db.settle_balance, a, a, 0, contains="yourself")
    assert db.list_shared(c) == []
    # a top-level category is accepted and filed under its 'Other' subcategory
    with db.connect() as conn:
        top = conn.execute("SELECT id FROM categories WHERE user_id = ? AND name = 'Food' "
                           "AND parent_id IS NULL", (b,)).fetchone()["id"]
    db.accept_shared(b, sid, top)
    assert db.list_transactions(b)[0]["category_name"] == "Other"


def test_uneven_multi_payer() -> None:
    a, b, c = fresh()
    # A pays 60, B pays 40; owed 50 / 25 / 25
    split(a, 10000, [(a, 6000, 5000), (b, 4000, 2500), (c, 0, 2500)], cat(a, "Groceries"))
    assert balance(a, c) == 1000 and balance(b, c) == 1500 and balance(a, b) == 0
    assert balance(c, a) == -1000 and balance(c, b) == -1500


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
