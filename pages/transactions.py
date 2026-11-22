"""Transactions page: add, filter, edit and delete transactions.

Amounts are typed in the base currency as positive numbers; the Income/Expense
toggle decides the sign that is stored. The table shows amounts converted to
the current display currency.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from nicegui import ui

import components
import db
import rates

# Quasar templates for the table. They only add the colour and the buttons;
# the buttons send "edit"/"delete" events that are handled in Python below.
AMOUNT_SLOT = (
    '<q-td :props="props" class="text-right">'
    '<span :class="props.row.amount_class">{{ props.value }}</span></q-td>'
)
ACTIONS_SLOT = """
<q-td :props="props" class="text-right">
  <q-btn flat round dense icon="edit" @click="() => $parent.$emit('edit', props.row)" />
  <q-btn flat round dense icon="delete" @click="() => $parent.$emit('delete', props.row)" />
</q-td>
"""

TABLE_COLUMNS = [
    {"name": "date", "label": "Date", "field": "date", "align": "left", "sortable": True},
    {"name": "description", "label": "Description", "field": "description", "align": "left"},
    {"name": "category", "label": "Category", "field": "category", "align": "left"},
    {"name": "amount", "label": "Amount", "field": "amount", "align": "right"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]


def _category_label(txn: dict[str, Any]) -> str:
    """'Food > Groceries', or just 'Food > Other' when no subcategory is used."""
    if txn["parent_name"]:
        return f"{txn['parent_name']} > {txn['category_name']}"
    return txn["category_name"]


class TransactionForm:
    """The input fields for one transaction, used for both adding and editing."""

    def __init__(self, user_id: int, currency: str, initial: dict[str, Any] | None = None) -> None:
        self.user_id = user_id
        kind, top_id, sub_id = "expense", None, None
        day, amount, description = date.today().isoformat(), None, ""
        if initial:
            kind = initial["kind"]
            top_id = initial["parent_id"] or initial["category_id"]
            sub_id = initial["category_id"] if initial["parent_id"] else None
            day, description = initial["date"], initial["description"]
            amount = abs(initial["amount_cents"]) / 100

        with ui.row().classes("w-full items-start gap-4"):
            self.date = ui.input("Date", value=day).props("type=date")
            self.kind = ui.toggle(
                {"expense": "Expense", "income": "Income"}, value=kind,
                on_change=self._on_kind_change,
            )
            self.amount = ui.number(f"Amount ({currency})", value=amount, step=0.01, format="%.2f")
            self.category = ui.select(
                self._top_options(kind), value=top_id, label="Category",
                on_change=self._on_category_change,
            ).classes("w-48")
            self.subcategory = ui.select(
                self._sub_options(top_id), value=sub_id, label="Subcategory (default: Other)",
                clearable=True,
            ).classes("w-52")
            self.description = ui.input("Description", value=description).classes("grow")

    def _top_options(self, kind: str) -> dict[int, str]:
        """Top-level categories of the chosen type."""
        return {c["id"]: c["name"] for c in db.list_top_level(self.user_id, kind)}

    def _sub_options(self, top_id: int | None) -> dict[int, str]:
        """Subcategories of the chosen category."""
        if top_id is None:
            return {}
        return {c["id"]: c["name"] for c in db.list_subcategories(self.user_id, top_id)}

    def _on_kind_change(self, event: Any) -> None:
        self.category.set_options(self._top_options(event.value), value=None)
        self.subcategory.set_options({}, value=None)

    def _on_category_change(self, event: Any) -> None:
        self.subcategory.set_options(self._sub_options(event.value), value=None)

    def read(self) -> tuple[str, int, int, str]:
        """Validate the fields; return (date, signed cents, category id, description).

        Raises ``db.DataError`` with a friendly message if something is wrong.
        """
        day = (self.date.value or "").strip()
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise db.DataError("Please pick a valid date.") from exc
        if self.amount.value is None:
            raise db.DataError("Please enter an amount.")
        cents = rates.amount_to_cents(self.amount.value)
        if cents <= 0:
            raise db.DataError("The amount must be greater than zero.")
        if self.category.value is None:
            raise db.DataError("Please choose a category.")
        category_id = self.subcategory.value or self.category.value
        signed = cents if self.kind.value == "income" else -cents
        return day, signed, category_id, (self.description.value or "").strip()

    def clear(self) -> None:
        """Reset amount and description, keeping date and category for quick repeat entry."""
        self.amount.value = None
        self.description.value = ""


@ui.page("/transactions")
async def transactions_page() -> None:
    """Render the transactions page."""
    ctx = await components.build_layout("Transactions")
    if ctx is None:
        return
    user_id = ctx.user["id"]
    shown: dict[int, dict[str, Any]] = {}  # transactions currently in the table, by id

    @ui.refreshable
    def transaction_table() -> None:
        txns = db.list_transactions(
            user_id,
            date_from=date_from.value or None,
            date_to=date_to.value or None,
            category_id=category_filter.value,
            search=search.value,
        )
        shown.clear()
        shown.update({t["id"]: t for t in txns})

        if not txns:
            if db.transaction_count(user_id) == 0:
                components.info_message("No transactions yet. Add your first one above.", "receipt_long")
            else:
                components.info_message("No transactions match the filters.", "filter_alt_off")
            return

        net = sum(t["amount_cents"] for t in txns)
        ui.label(f"{len(txns)} transaction(s), net {ctx.display.money(net)}").classes("text-grey-7")
        rows = [
            {
                "id": t["id"],
                "date": t["date"],
                "description": t["description"],
                "category": _category_label(t),
                "amount": ctx.display.money(t["amount_cents"]),
                "amount_class": components.amount_class(t["amount_cents"]),
            }
            for t in txns
        ]
        table = ui.table(columns=TABLE_COLUMNS, rows=rows, row_key="id", pagination=15)
        table.props("flat bordered").classes("w-full")
        table.add_slot("body-cell-amount", AMOUNT_SLOT)
        table.add_slot("body-cell-actions", ACTIONS_SLOT)
        table.on("edit", lambda e: edit_transaction(e.args["id"]))
        table.on("delete", lambda e: delete_transaction(e.args["id"]))

    def add_transaction() -> None:
        try:
            tx_date, cents, category_id, description = form.read()
            db.add_transaction(user_id, tx_date, cents, category_id, description)
        except db.DataError as exc:
            components.notify_error(exc)
            return
        ui.notify("Transaction added.", type="positive")
        form.clear()
        transaction_table.refresh()

    def edit_transaction(transaction_id: int) -> None:
        txn = shown.get(transaction_id)
        if txn is None:
            return
        dialog = components.new_dialog()
        with dialog, ui.card().classes("w-full max-w-5xl"):
            ui.label("Edit transaction").classes("text-h6")
            edit_form = TransactionForm(user_id, ctx.base_currency, txn)

            def save() -> None:
                try:
                    tx_date, cents, category_id, description = edit_form.read()
                    db.update_transaction(user_id, transaction_id, tx_date, cents, category_id, description)
                except db.DataError as exc:
                    components.notify_error(exc)
                    return
                dialog.close()
                ui.notify("Transaction updated.", type="positive")
                transaction_table.refresh()

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Save", on_click=save)
        dialog.open()

    def delete_transaction(transaction_id: int) -> None:
        txn = shown.get(transaction_id)
        if txn is None:
            return

        def delete() -> None:
            db.delete_transaction(user_id, transaction_id)
            ui.notify("Transaction deleted.", type="positive")
            transaction_table.refresh()

        text = f"Delete the {ctx.display.money(txn['amount_cents'])} transaction on {txn['date']}?"
        components.confirm_dialog("Delete transaction", text, "Delete", delete)

    def clear_filters() -> None:
        date_from.value = None
        date_to.value = None
        category_filter.value = None
        search.value = ""

    with components.content():
        ui.label("Transactions").classes("text-h5")

        with ui.card().classes("w-full"):
            ui.label("Add a transaction").classes("text-subtitle1 text-bold")
            form = TransactionForm(user_id, ctx.base_currency)
            form.description.on("keydown.enter", add_transaction)
            if ctx.display.code != ctx.base_currency:
                ui.label(
                    f"Amounts are entered in {ctx.base_currency}, your base currency. "
                    f"The table below shows {ctx.display.code}."
                ).classes("text-caption text-grey-7")
            ui.button("Add transaction", icon="add", on_click=add_transaction)

        with ui.card().classes("w-full"):
            ui.label("Filters").classes("text-subtitle1 text-bold")
            with ui.row().classes("w-full items-center gap-4"):
                date_from = ui.input("From", on_change=lambda: transaction_table.refresh())
                date_from.props("type=date clearable")
                date_to = ui.input("To", on_change=lambda: transaction_table.refresh())
                date_to.props("type=date clearable")
                category_filter = ui.select(
                    db.category_options(user_id), label="Category", clearable=True, with_input=True,
                    on_change=lambda: transaction_table.refresh(),
                ).classes("w-56")
                search = ui.input("Search description", on_change=lambda: transaction_table.refresh())
                search.props("clearable debounce=300").classes("grow")
                ui.button("Clear", icon="filter_alt_off", on_click=clear_filters).props("flat")

        transaction_table()
