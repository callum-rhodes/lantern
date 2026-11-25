"""Tests for the arithmetic behind the split dialog (``shared_ui.SplitModel``).

Run from the project folder:   python test_split_model.py
No database is touched.
"""
from __future__ import annotations

from decimal import Decimal

from shared_ui import OWED, PAID, PCT, VALUE, SplitModel

A, B, C = 1, 2, 3


def model(total_cents: int = 10000, people: tuple[int, ...] = (A, B)) -> SplitModel:
    m = SplitModel(A, total_cents)
    m.set_people(list(people))
    return m


def test_starts_as_even_split_with_creator_paying() -> None:
    m = model()
    assert m.cents(OWED) == [5000, 5000]
    assert m.cents(PAID) == [10000, 0]
    assert m.problem() is None
    assert m.participants() == [
        {"user_id": A, "paid_cents": 10000, "owed_cents": 5000},
        {"user_id": B, "paid_cents": 0, "owed_cents": 5000},
    ]


def test_creator_alone_cannot_save() -> None:
    m = SplitModel(A, 10000)
    assert "other person" in (m.problem() or "")
    assert model(0).problem() == "Enter a total above zero."


def test_thirds_give_the_extra_cent_to_the_creator() -> None:
    m = model(10000, (A, B, C))
    assert m.cents(OWED) == [3334, 3333, 3333]
    assert m.values[OWED] == {A: 33.34, B: 33.33, C: 33.33}
    m.set_mode(OWED, PCT)                       # auto column: refilled in percent
    assert m.values[OWED] == {A: 33.34, B: 33.33, C: 33.33}
    assert m.remaining(OWED) == (Decimal("0.00"), True)
    assert m.cents(OWED) == [3334, 3333, 3333]


def test_creator_is_always_kept_and_first() -> None:
    m = model(10000, (C, B))
    assert m.people == [A, C, B]
    m.set_people([B])
    assert m.people == [A, B]


def test_auto_columns_follow_total_and_people() -> None:
    m = model(10000, (A, B))
    m.set_total(9000)
    assert m.cents(OWED) == [4500, 4500] and m.cents(PAID) == [9000, 0]
    m.set_people([A, B, C])
    assert m.cents(OWED) == [3000, 3000, 3000]


def test_typing_stops_auto_and_shows_what_is_left() -> None:
    m = model(10000, (A, B))
    m.set_value(OWED, A, 60.0)                  # B still shows 50.00
    assert m.remaining(OWED) == (Decimal("-10.00"), False)
    m.set_value(OWED, B, 40.0)
    assert m.remaining(OWED) == (Decimal("0.00"), True)
    m.set_total(20000)                          # typed column no longer follows the total
    assert m.values[OWED] == {A: 60.0, B: 40.0}
    assert m.remaining(OWED) == (Decimal("100.00"), False)
    assert m.problem() is not None
    m.refill(OWED)                              # "Split evenly" button
    assert m.cents(OWED) == [10000, 10000]


def test_currency_to_percent_is_exact() -> None:
    m = model(10000, (A, B, C))
    for uid, v in ((A, 33.34), (B, 33.33), (C, 33.33)):
        m.set_value(OWED, uid, v)
    m.set_mode(OWED, PCT)
    assert m.values[OWED] == {A: 33.34, B: 33.33, C: 33.33}
    assert m.remaining(OWED) == (Decimal("0.00"), True)
    m = model(7000, (A, B))
    m.set_value(OWED, A, 40.0)
    m.set_value(OWED, B, 30.0)
    m.set_mode(OWED, PCT)
    assert m.values[OWED] == {A: 57.14, B: 42.86}            # adds up to exactly 100
    m.set_mode(OWED, VALUE)
    assert m.cents(OWED) == [4000, 3000]


def test_percent_to_currency_uses_largest_remainder() -> None:
    m = model(10000, (A, B, C))
    m.set_mode(OWED, PCT)
    for uid in (A, B, C):
        m.set_value(OWED, uid, 33.33)            # sums to 99.99, within tolerance
    assert m.remaining(OWED)[1] is True
    assert m.cents(OWED) == [3334, 3333, 3333]
    m.set_mode(OWED, VALUE)
    assert m.values[OWED] == {A: 33.34, B: 33.33, C: 33.33}


def test_incomplete_values_convert_without_hiding_the_gap() -> None:
    m = model(10000, (A, B))
    m.set_value(OWED, A, 30.0)
    m.set_value(OWED, B, None)
    m.set_mode(OWED, PCT)
    assert m.values[OWED] == {A: 30.0, B: 0.0} or m.values[OWED] == {A: 30.0, B: 0.0}
    assert m.remaining(OWED) == (Decimal("70.00"), False)


def test_mixed_modes_and_multiple_payers() -> None:
    m = model(10000, (A, B, C))
    m.set_mode(PAID, PCT)
    m.set_value(PAID, A, 60.0)
    m.set_value(PAID, B, 40.0)
    m.set_value(PAID, C, 0.0)
    assert m.cents(PAID) == [6000, 4000, 0]
    assert m.problem() is None                   # C owes a share, so is fine
    m.set_mode(OWED, PCT)
    m.set_value(OWED, A, 50.0)
    m.set_value(OWED, B, 25.0)
    m.set_value(OWED, C, 25.0)
    assert m.cents(OWED) == [5000, 2500, 2500]
    assert m.problem() is None


def test_someone_with_nothing_blocks_saving() -> None:
    m = model(10000, (A, B, C))
    m.set_value(OWED, A, 50.0)
    m.set_value(OWED, B, 50.0)
    m.set_value(OWED, C, 0.0)                    # C paid 0 (creator paid all) and owes 0
    assert "paid or owe" in (m.problem() or "")


def test_bad_numbers_are_invalid_not_crashes() -> None:
    m = model(10000, (A, B))
    m.set_value(OWED, A, -5.0)
    assert m.cents(OWED) is None and m.remaining(OWED)[1] is False and m.problem() is not None
    m.refill(OWED)
    m.set_mode(OWED, PCT)
    m.set_value(OWED, A, 90.0)
    m.set_value(OWED, B, 20.0)                   # 110%
    assert m.cents(OWED) is None and m.remaining(OWED) == (Decimal("-10.0"), False)


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
