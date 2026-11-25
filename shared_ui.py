"""Reusable widgets for shared transactions (like ``components.py``, but for this feature).

* ``open_split_dialog(ctx, prefill, on_saved)``: the dialog that sets up who paid and who
  owes what, and creates the shared transaction.
* ``pending_shared_card(ctx, on_change)``: the card listing shares waiting for the user to
  accept (choosing a category) or reject.

All the arithmetic of the dialog lives in ``SplitModel`` (no UI code), so it can be tested
on its own. Everything is entered in the base currency, like the rest of the app.
"""
from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from nicegui import ui

import components
import db
import rates
import splits

PAID, OWED = "paid", "owed"
VALUE, PCT = "value", "pct"
COLUMNS = (PAID, OWED)
TITLES = {PAID: "Paid", OWED: "Owes"}


# --------------------------------------------------------------------------
# The numbers behind the dialog
# --------------------------------------------------------------------------

class SplitModel:
    """Who is in the split and what each person paid and owes.

    Each column (Paid, Owes) is either in currency units (``VALUE``) or percentages
    (``PCT``). ``values`` holds what the boxes show. A column is *auto* until the user
    types in it: Owes then follows an even split, Paid follows "creator paid all", and
    both are recalculated when the total or the people change. The buttons switch a
    column back to auto.
    """

    def __init__(self, creator_id: int, total_cents: int) -> None:
        self.creator_id = creator_id
        self.total_cents = max(int(total_cents), 0)
        self.people: list[int] = [creator_id]
        self.mode = {PAID: VALUE, OWED: VALUE}
        self.values: dict[str, dict[int, float | None]] = {PAID: {}, OWED: {}}
        self.auto = {PAID: True, OWED: True}
        for col in COLUMNS:
            self.refill(col)

    # ---- changing things ------------------------------------------------------
    def set_total(self, total_cents: int) -> None:
        self.total_cents = max(int(total_cents), 0)
        for col in COLUMNS:
            if self.auto[col]:
                self.refill(col)

    def set_people(self, selected: list[int]) -> None:
        """New selection; the creator is always kept and listed first."""
        people = [self.creator_id] + [u for u in dict.fromkeys(selected) if u != self.creator_id]
        self.people = people
        for col in COLUMNS:
            if self.auto[col]:
                self.refill(col)
            else:
                self.values[col] = {u: self.values[col].get(u) for u in people}

    def set_value(self, col: str, user_id: int, value: float | None) -> None:
        """The user typed in a box."""
        self.values[col][user_id] = value
        self.auto[col] = False

    def refill(self, col: str) -> None:
        """Even split (Owes) or creator paid all (Paid), in the column's current mode."""
        n, total = len(self.people), self.total_cents
        if col == OWED:
            pcts = [c / 100 for c in splits.shares(10000, [1] * n)]
            cents = splits.shares(total, [1] * n) if total > 0 else None
        else:
            pcts = [100.0] + [0.0] * (n - 1)
            cents = [total] + [0] * (n - 1) if total > 0 else None
        if self.mode[col] == PCT:
            shown: list[float | None] = list(pcts)
        else:
            shown = [None] * n if cents is None else [c / 100 for c in cents]
        self.values[col] = dict(zip(self.people, shown))
        self.auto[col] = True

    def set_mode(self, col: str, mode: str) -> None:
        """Switch a column between currency and percent, converting what is entered."""
        if mode == self.mode[col]:
            return
        if self.auto[col]:
            self.mode[col] = mode
            self.refill(col)
            return
        old = self.values[col]
        total = self.total_cents
        if total <= 0:
            new: list[float | None] = [None] * len(self.people)
        elif mode == PCT:  # currency -> percent
            cents = [self._cell_cents(old.get(u)) for u in self.people]
            if sum(cents) == total and min(cents) >= 0:
                new = [s / 100 for s in splits.shares(10000, cents)]  # adds up to exactly 100
            else:
                new = [float((Decimal(c) * 100 / total).quantize(Decimal("0.01"), ROUND_HALF_UP))
                       for c in cents]
        else:              # percent -> currency
            pcts = [Decimal(str(old.get(u) or 0)) for u in self.people]
            try:
                cents = splits.percents_to_cents(total, pcts)
            except splits.SplitError:
                cents = [int((p * total / 100).quantize(Decimal(1), ROUND_HALF_UP)) for p in pcts]
            new = [c / 100 for c in cents]
        self.values[col] = dict(zip(self.people, new))
        self.mode[col] = mode

    # ---- reading it back ------------------------------------------------------
    @staticmethod
    def _cell_cents(value: float | None) -> int:
        try:
            return 0 if value is None else rates.amount_to_cents(value)
        except db.DataError:
            return -1

    def cents(self, col: str) -> list[int] | None:
        """Each person's amount in cents, or None if the column is not valid yet."""
        cells = [self.values[col].get(u) for u in self.people]
        if self.total_cents <= 0 or any(v is not None and v < 0 for v in cells):
            return None
        if self.mode[col] == VALUE:
            result = [self._cell_cents(v) for v in cells]
            return None if min(result) < 0 else result
        try:
            return splits.percents_to_cents(self.total_cents, [Decimal(str(v or 0)) for v in cells])
        except splits.SplitError:
            return None

    def remaining(self, col: str) -> tuple[Decimal, bool]:
        """(what is left to assign, whether the column is complete).

        Currency units in VALUE mode, percentage points in PCT mode.
        """
        cells = [self.values[col].get(u) for u in self.people]
        if self.mode[col] == VALUE:
            cents = [self._cell_cents(v) for v in cells]
            left = self.total_cents - sum(c for c in cents if c >= 0)
            return Decimal(left) / 100, left == 0 and self.total_cents > 0 and min(cents) >= 0
        left = Decimal(100) - sum((Decimal(str(v or 0)) for v in cells), Decimal(0))
        ok = abs(left) <= Decimal("0.05") and self.total_cents > 0 and not any(
            v is not None and v < 0 for v in cells)
        return left, ok

    def problem(self) -> str | None:
        """Why the split can't be saved yet, or None if it can."""
        if self.total_cents <= 0:
            return "Enter a total above zero."
        if len(self.people) < 2:
            return "Pick at least one other person."
        paid, owed = self.cents(PAID), self.cents(OWED)
        if paid is None or owed is None or not self.remaining(PAID)[1] or not self.remaining(OWED)[1]:
            return "Paid and Owes must each add up to the total."
        if any(p + o == 0 for p, o in zip(paid, owed)):
            return "Everyone in the split must have paid or owe something."
        return None

    def participants(self) -> list[dict[str, int]]:
        """Rows for ``db.create_shared_transaction`` (only valid when ``problem()`` is None)."""
        paid, owed = self.cents(PAID), self.cents(OWED)
        assert paid is not None and owed is not None
        return [{"user_id": u, "paid_cents": p, "owed_cents": o}
                for u, p, o in zip(self.people, paid, owed)]


# --------------------------------------------------------------------------
# Split dialog
# --------------------------------------------------------------------------

class _SplitDialog:
    def __init__(self, ctx: components.PageContext, prefill: dict[str, Any],
                 on_saved: Callable[[], None]) -> None:
        self.ctx, self.prefill, self.on_saved = ctx, prefill, on_saved
        self.me = ctx.user["id"]
        self.currency = ctx.base_currency
        self.symbol = rates.CURRENCY_SYMBOLS.get(self.currency, self.currency)
        self.names = {u["id"]: u["name"] for u in db.list_users()}
        total = prefill.get("total")
        self.model = SplitModel(self.me, rates.amount_to_cents(total) if total else 0)
        self.remaining_labels: dict[str, ui.label] = {}

    def open(self) -> None:
        dialog = components.new_dialog()
        self.dialog = dialog
        with dialog, ui.card().classes("w-full max-w-4xl gap-3"):
            ui.label("Split this expense").classes("text-h6")
            ui.label(f"{self.prefill['date']} · {self.prefill.get('description') or '(no description)'}"
                     f" · amounts in {self.currency}").classes("text-caption text-grey-7")

            self.total = ui.number(
                f"Total ({self.currency})", value=self.model.total_cents / 100 or None,
                step=0.01, format="%.2f", min=0, on_change=self._total_changed,
            ).classes("w-48")
            options = {uid: name + (" (you)" if uid == self.me else "")
                       for uid, name in self.names.items()}
            self.people = ui.select(
                options, value=[self.me], multiple=True, label="Who is in the split?",
                on_change=self._people_changed,
            ).props("use-chips").classes("w-full")

            # Column headers: mode toggle, helper button and (below) what is left to assign.
            with ui.row().classes("items-start gap-4 q-mt-sm"):
                ui.label("").classes("w-36")
                for col in COLUMNS:
                    with ui.column().classes("w-56 gap-1"):
                        ui.label(TITLES[col]).classes("text-subtitle1 text-bold")
                        ui.toggle({VALUE: self.symbol, PCT: "%"}, value=self.model.mode[col],
                                  on_change=lambda e, c=col: self._mode_changed(c, e.value)).props("dense")
                        if col == OWED:
                            ui.button("Split evenly", on_click=lambda: self._helper(OWED)) \
                                .props("flat dense no-caps")
                        else:
                            ui.button("Creator paid all", on_click=lambda: self._helper(PAID)) \
                                .props("flat dense no-caps")

            @ui.refreshable
            def rows() -> None:
                for uid in self.model.people:
                    with ui.row().classes("items-center gap-4"):
                        ui.label(self.names.get(uid, "?")).classes("w-36")
                        for col in COLUMNS:
                            self._number(col, uid)

            self.rows = rows
            rows()

            with ui.row().classes("items-center gap-4"):
                ui.label("Left to assign").classes("w-36 text-caption")
                for col in COLUMNS:
                    self.remaining_labels[col] = ui.label().classes("w-56 text-caption")
            self.hint = ui.label().classes("text-caption text-grey-7")

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                self.save_button = ui.button("Create shared transaction", on_click=self._save)
            self._update_status()
        dialog.open()

    # ---- widgets --------------------------------------------------------------
    def _number(self, col: str, uid: int) -> None:
        pct = self.model.mode[col] == PCT
        ui.number(
            value=self.model.values[col].get(uid), step=0.01, format="%.2f", min=0,
            prefix=None if pct else self.symbol, suffix="%" if pct else None,
            on_change=lambda e, c=col, u=uid: self._edited(c, u, e.value),
        ).props("dense outlined").classes("w-56")

    def _update_status(self) -> None:
        for col in COLUMNS:
            left, ok = self.model.remaining(col)
            if self.model.mode[col] == PCT:
                text = f"{left:.2f}%"
            else:
                text = rates.format_money(int(left * 100), self.currency)
            label = self.remaining_labels[col]
            label.set_text(text)
            label.classes(replace="w-56 text-caption " + ("text-positive" if ok else "text-negative"))
        problem = self.model.problem()
        self.hint.set_text(problem or "")
        self.save_button.set_enabled(problem is None)

    # ---- events ---------------------------------------------------------------
    def _edited(self, col: str, uid: int, value: float | None) -> None:
        if value == self.model.values[col].get(uid):
            return  # a rebuild or programmatic set, not the user typing
        self.model.set_value(col, uid, value)
        self._update_status()

    def _total_changed(self, event: Any) -> None:
        try:
            cents = rates.amount_to_cents(event.value) if event.value is not None else 0
        except db.DataError:
            cents = 0
        self.model.set_total(cents)
        self.rows.refresh()
        self._update_status()

    def _people_changed(self, event: Any) -> None:
        chosen = list(event.value or [])
        if self.me not in chosen:  # the creator can't be removed
            self.people.value = [self.me] + chosen
            return
        self.model.set_people(chosen)
        self.rows.refresh()
        self._update_status()

    def _mode_changed(self, col: str, mode: str) -> None:
        self.model.set_mode(col, mode)
        self.rows.refresh()
        self._update_status()

    def _helper(self, col: str) -> None:
        self.model.refill(col)
        self.rows.refresh()
        self._update_status()

    def _save(self) -> None:
        problem = self.model.problem()
        if problem:
            ui.notify(problem, type="warning")
            return
        try:
            db.create_shared_transaction(
                self.me, self.prefill["date"], self.prefill.get("description", ""),
                self.model.total_cents, self.prefill.get("category_id"), self.model.participants(),
            )
        except db.DataError as exc:
            components.notify_error(exc)
            return
        self.dialog.close()
        ui.notify("Shared transaction created. The others will see it as pending.", type="positive")
        self.on_saved()


def open_split_dialog(
    ctx: components.PageContext, prefill: dict[str, Any], on_saved: Callable[[], None]
) -> None:
    """Open the split dialog.

    ``prefill`` has ``date`` (YYYY-MM-DD), ``description``, ``total`` (in the base currency,
    e.g. 12.5) and ``category_id`` (the creator's expense category). ``on_saved`` runs after
    the shared transaction has been created.
    """
    _SplitDialog(ctx, prefill, on_saved).open()


# --------------------------------------------------------------------------
# Pending shares
# --------------------------------------------------------------------------

def pending_shared_card(ctx: components.PageContext, on_change: Callable[[], None]) -> None:
    """Card listing shared transactions waiting for the user. Draws nothing if there are none.

    The user chooses an expense category and accepts (which adds a personal transaction of
    their share) or rejects (which cancels the split for everyone, after confirming).
    Call it inside a ``ui.refreshable``; ``on_change`` runs after each accept or reject.
    """
    user_id = ctx.user["id"]
    items = db.list_shared_pending(user_id)
    if not items:
        return
    options = db.category_options(user_id, kind="expense")
    selects: dict[int, ui.select] = {}

    def accept(item: dict[str, Any]) -> None:
        category_id = selects[item["id"]].value
        if category_id is None:
            ui.notify("Choose a category first.", type="warning")
            return
        try:
            db.accept_shared(user_id, item["id"], category_id)
        except db.DataError as exc:
            components.notify_error(exc)
            return
        on_change()

    def reject(item: dict[str, Any]) -> None:
        def do_reject() -> None:
            db.reject_shared(user_id, item["id"])
            ui.notify("Rejected. The split has been cancelled for everyone.", type="positive")
            on_change()

        components.confirm_dialog(
            "Reject shared transaction",
            f"Rejecting cancels this split for everyone: {item['creator_name']}'s and everyone "
            "else's copies are removed and it is kept as a 'Rejected' record. Continue?",
            "Reject", do_reject,
        )

    with ui.card().classes("w-full"):
        ui.label(f"Shared transactions awaiting your review ({len(items)})") \
            .classes("text-subtitle1 text-bold")
        ui.label("Choose a category for your share, then accept. You can't edit the amounts.") \
            .classes("text-caption text-grey-7")
        for item in items:
            with ui.row().classes("w-full items-center gap-4"):
                ui.label(item["date"]).classes("w-24")
                ui.label(item["description"] or "(no description)").classes("grow")
                ui.label(item["creator_name"]).classes("w-28")
                with ui.column().classes("w-32 gap-0"):
                    ui.label(f"Total {ctx.display.money(item['total_cents'])}").classes("text-caption")
                    ui.label(f"Your share {ctx.display.money(item['owed_cents'])}") \
                        .classes("text-negative text-bold")
                selects[item["id"]] = ui.select(
                    options, label="Category", with_input=True,
                ).classes("w-56")
                ui.button(icon="check", on_click=lambda _, i=item: accept(i)) \
                    .props("flat round dense color=positive").tooltip("Accept")
                ui.button(icon="close", on_click=lambda _, i=item: reject(i)) \
                    .props("flat round dense color=negative").tooltip("Reject")
