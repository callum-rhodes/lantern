"""Maths for shared transactions: turning percentages into cents and working out who owes whom.

Pure functions only: no database, no UI, no imports from the rest of the app (so ``db.py``
can import this module without a circular import). Problems raise ``SplitError``; the
callers in ``db.py`` and ``shared_ui.py`` turn that into a friendly ``db.DataError``.

All money is integer minor units (pence/cents). Rounding uses exact ``Fraction``
arithmetic, so results never depend on floating-point error.
"""
from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction

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
        shares = _largest_remainder(-nets[debtor], weights)
        for creditor, cents in zip(creditors, shares):
            if cents:
                debts[(debtor, creditor)] = cents
    return debts
