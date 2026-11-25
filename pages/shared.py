"""Shared Transactions page: balances with other users, settling up, and the activity list.

* Balances card: one line per user you have shared with ("B owes you", "You owe B" or
  "All settled with B"), with a "Mark as settled" button and a confirmation dialog.
* Activity table (newest first): shared transactions and settlement entries. Shared rows
  open a details dialog and can be deleted where the rules allow (db/shared_transactions.list_shared says which);
  settlement entries can be deleted by either party.

Accepting or rejecting a share that is waiting for you happens on the Transactions page.
All money is shown in the current display currency; settling records the base-currency
amount that was on screen, and fails with "balance changed" if it has moved since.
"""
from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
from nicegui import app, ui

import components
from db import DataError, shared_transactions
import rates

TABLE_COLUMNS = [
    {"name": "date", "label": "Date", "field": "date", "align": "left"},
    {"name": "description", "label": "Description", "field": "description", "align": "left"},
    {"name": "total", "label": "Total", "field": "total", "align": "right"},
    {"name": "participants", "label": "Paid / owes", "field": "participants", "align": "left"},
    {"name": "status", "label": "Your status", "field": "status", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]

OWED_COLOR = "#21ba45"   # they owe you
OWING_COLOR = "#c10015"  # you owe them
SETTLED_COLOR = "#9e9e9e"

PARTICIPANTS_SLOT = (
    '<q-td :props="props" style="white-space: pre-line">{{ props.value }}</q-td>'
)
STATUS_SLOT = (
    '<q-td :props="props"><span :class="props.row.status_class">{{ props.value }}</span></q-td>'
)
ACTIONS_SLOT = """
<q-td :props="props" class="text-right">
  <q-btn v-if="props.row.has_details" flat round dense icon="info"
         @click="() => $parent.$emit('details', props.row)">
    <q-tooltip>Details</q-tooltip>
  </q-btn>
  <q-btn v-if="props.row.can_delete" flat round dense icon="delete"
         @click="() => $parent.$emit('delete', props.row)">
    <q-tooltip>Delete</q-tooltip>
  </q-btn>
</q-td>
"""


@ui.page("/shared")
async def shared_page() -> None:
    """Render the Shared Transactions page."""
    ctx = await components.build_layout("Shared Balances")
    if ctx is None:
        return
    user_id = ctx.user["id"]
    money = ctx.display.money
    items: dict[str, dict[str, Any]] = {}  # rows currently in the table, by key

    def refresh_all() -> None:
        balances_card.refresh()
        balance_chart.refresh()
        activity.refresh()

    # ---- balances ----------------------------------------------------------------
    def settle_dialog(balance: dict[str, Any]) -> None:
        cents, name = balance["cents"], balance["name"]
        amount = money(abs(cents))
        sentence = f"{name} owes you {amount}" if cents > 0 else f"You owe {name} {amount}"
        dialog = components.new_dialog()
        with dialog, ui.card().classes("w-96 gap-3"):
            ui.label("Mark as settled").classes("text-h6")
            ui.label(
                f"{sentence}. Mark it as settled? This records a settlement of {amount} "
                f"between you and {name}. Either of you can delete it later, which brings "
                "the old balance back."
            )
            if ctx.display.code != ctx.base_currency:
                ui.label(f"Recorded as {rates.format_money(abs(cents), ctx.base_currency)}.") \
                    .classes("text-caption text-grey-7")

            def confirm() -> None:
                try:
                    shared_transactions.settle_balance(user_id, balance["user_id"], cents)
                except DataError as exc:
                    components.notify_error(exc)  # e.g. "balance changed": show the new amount
                    dialog.close()
                    refresh_all()
                    return
                dialog.close()
                ui.notify(f"Settled with {name}.", type="positive")
                refresh_all()

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Mark as settled", on_click=confirm)
        dialog.open()

    @ui.refreshable
    def balances_card() -> None:
        balances = shared_transactions.shared_balances(user_id)
        with ui.card().classes("w-full"):
            ui.label("Balances").classes("text-h6")
            if not balances:
                components.info_message(
                    "Nothing shared yet. Turn on 'Share with other users' when adding a transaction.",
                    "group")
                return
            for balance in balances:
                cents, name = balance["cents"], balance["name"]
                with ui.row().classes("w-full items-center gap-4"):
                    if cents > 0:
                        ui.label(f"{name} owes you {money(cents)}").classes("grow text-positive")
                    elif cents < 0:
                        ui.label(f"You owe {name} {money(-cents)}").classes("grow text-negative")
                    else:
                        ui.label(f"All settled with {name}").classes("grow text-grey-7")
                    if cents != 0:
                        ui.button("Mark as settled", icon="done_all",
                                  on_click=lambda _, b=balance: settle_dialog(b)).props("flat")
            ui.label("Balances include shares that are still waiting to be accepted.") \
                .classes("text-caption text-grey-7")

    @ui.refreshable
    def balance_chart() -> None:
        balances = shared_transactions.shared_balances(user_id)
        if not balances:
            return
        names = [b["name"] for b in balances]
        values = [ctx.display.major(b["cents"]) for b in balances]
        colours = [OWED_COLOR if b["cents"] > 0 else OWING_COLOR if b["cents"] < 0 else SETTLED_COLOR
                   for b in balances]
        fig = go.Figure(go.Bar(
            x=values, y=names, orientation="h", marker_color=colours,
            text=[money(b["cents"]) if b["cents"] < 0 else money(b["cents"], show_plus=True)
                  for b in balances],
            textposition="outside", cliponaxis=False,
            hovertemplate="%{y}: %{x:+,.2f}<extra></extra>",
        ))
        dark = bool(app.storage.user.get("dark_mode"))
        fig.add_vline(x=0, line_width=1, line_color="#757575")
        # Symmetric range so zero is always in the middle (padded so the labels fit).
        limit = max((abs(v) for v in values), default=0) * 1.35 or 1.0
        fig.update_layout(
            template="plotly_dark" if dark else "plotly_white",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            height=max(180, 55 * len(balances) + 90),
            margin={"l": 20, "r": 20, "t": 10, "b": 40},
            xaxis_title=f"Balance ({ctx.display.code})",
            yaxis={"autorange": "reversed"},  # same order as the Balances card
            xaxis={"title": f"Balance ({ctx.display.code})",
                   "range": [-limit, limit],
                   "zeroline": False},
            showlegend=False,
        )
        with ui.card().classes("w-full"):
            ui.label("Balance chart").classes("text-h6")
            ui.label("Green (+) means they owe you, red (−) means you owe them.") \
                .classes("text-caption text-grey-7")
            ui.plotly(fig).classes("w-full")

    # ---- activity ----------------------------------------------------------------
    def _people_text(item: dict[str, Any]) -> str:
        lines = []
        for p in item["participants"]:
            who = "You" if p["user_id"] == user_id else p["name"]
            lines.append(f"{who}: paid {money(p['paid_cents'])}, owes {money(p['owed_cents'])}")
        return "\n".join(lines)

    def _status(item: dict[str, Any]) -> tuple[str, str]:
        if item["my_status"] == "rejected":
            who = "you" if item["rejected_by"] == user_id else item["rejected_by_name"]
            return f"Rejected by {who}", "text-negative"
        if item["my_status"] == "pending":
            return "Pending", "text-orange-8"
        return "Accepted", "text-positive"

    def _shared_row(item: dict[str, Any]) -> dict[str, Any]:
        status, status_class = _status(item)
        return {
            "key": f"s{item['id']}", "date": item["date"],
            "description": item["description"] or "(no description)",
            "total": money(item["total_cents"]), "participants": _people_text(item),
            "status": status, "status_class": status_class,
            "has_details": True, "can_delete": item["can_delete"],
            "_sort": (item["date"], item["created_at"], item["id"]),
        }

    def _settlement_row(item: dict[str, Any]) -> dict[str, Any]:
        other = item["to_name"] if item["from_user_id"] == user_id else item["from_name"]
        by = "you" if item["settled_by"] == user_id else item["settled_by_name"]
        return {
            "key": f"p{item['id']}", "date": item["created_at"][:10],
            "description": f"You and {other} settled {money(item['amount_cents'])}",
            "total": money(item["amount_cents"]), "participants": f"Marked as settled by {by}",
            "status": "Settled", "status_class": "text-grey-7",
            "has_details": False, "can_delete": True,
            "_sort": (item["created_at"][:10], item["created_at"], item["id"]),
        }

    def show_details(item: dict[str, Any]) -> None:
        status_word = {"pending": "Pending", "accepted": "Accepted"}
        rows = [{
            "who": "You" if p["user_id"] == user_id else p["name"],
            "paid": money(p["paid_cents"]), "owes": money(p["owed_cents"]),
            "status": "Cancelled" if item["status"] == "rejected" else status_word[p["status"]],
        } for p in item["participants"]]
        dialog = components.new_dialog()
        with dialog, ui.card().classes("w-full max-w-2xl gap-2"):
            ui.label(item["description"] or "(no description)").classes("text-h6")
            ui.label(f"{item['date']} · total {money(item['total_cents'])} · "
                     f"created by {'you' if item['creator_id'] == user_id else item['creator_name']}") \
                .classes("text-caption text-grey-7")
            if item["status"] == "rejected":
                who = "you" if item["rejected_by"] == user_id else item["rejected_by_name"]
                ui.label(f"Rejected by {who}. The split was cancelled and every personal copy "
                         "was removed. It doesn't count in balances.").classes("text-negative")
            elif item["my_status"] == "pending":
                with ui.row().classes("items-center gap-1"):
                    ui.label("Waiting for you.")
                    ui.link("Accept or reject it on the Transactions page", "/transactions")
            ui.table(
                columns=[
                    {"name": "who", "label": "Person", "field": "who", "align": "left"},
                    {"name": "paid", "label": "Paid", "field": "paid", "align": "right"},
                    {"name": "owes", "label": "Owes", "field": "owes", "align": "right"},
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                ],
                rows=rows, row_key="who",
            ).props("flat bordered dense hide-bottom").classes("w-full")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    def delete_item(row_key: str) -> None:
        item = items.get(row_key)
        if item is None:
            return
        if row_key.startswith("p"):
            def do_delete() -> None:
                shared_transactions.delete_settlement(user_id, item["id"])
                ui.notify("Settlement deleted. The previous balance is back.", type="positive")
                refresh_all()

            components.confirm_dialog(
                "Delete settlement",
                f"Delete this settlement of {money(item['amount_cents'])}? "
                "The balance between you goes back to what it was before.",
                "Delete", do_delete)
            return

        def do_delete_shared() -> None:
            shared_transactions.delete_shared(user_id, item["id"])
            ui.notify("Deleted.", type="positive")
            refresh_all()

        if item["status"] == "rejected":
            text = "Remove this rejected record? It disappears for everyone in the split."
        else:
            text = ("Delete this shared transaction for everyone? Every personal copy is removed, "
                    "no record is kept, and the balances change.")
        components.confirm_dialog("Delete shared transaction", text, "Delete", do_delete_shared)

    @ui.refreshable
    def activity() -> None:
        shared = shared_transactions.list_shared(user_id)
        settlements = shared_transactions.list_settlements(user_id)
        items.clear()
        items.update({f"s{s['id']}": s for s in shared})
        items.update({f"p{p['id']}": p for p in settlements})
        ui.label("Activity").classes("text-h6")
        if not items:
            components.info_message("No shared transactions or settlements yet.", "receipt_long")
            return
        if any(s["my_status"] == "pending" for s in shared):
            with ui.row().classes("items-center gap-1 text-grey-7"):
                ui.label("Items marked Pending are waiting for you on the")
                ui.link("Transactions page", "/transactions")
        rows = [_shared_row(s) for s in shared] + [_settlement_row(p) for p in settlements]
        rows.sort(key=lambda r: r["_sort"], reverse=True)
        for row in rows:
            row.pop("_sort")
        table = ui.table(columns=TABLE_COLUMNS, rows=rows, row_key="key", pagination=15)
        table.props("flat bordered").classes("w-full")
        table.add_slot("body-cell-participants", PARTICIPANTS_SLOT)
        table.add_slot("body-cell-status", STATUS_SLOT)
        table.add_slot("body-cell-actions", ACTIONS_SLOT)
        table.on("details", lambda e: show_details(items[e.args["key"]]))
        table.on("delete", lambda e: delete_item(e.args["key"]))

    with components.content():
        ui.label("Shared Transactions").classes("text-h5")
        balances_card()
        balance_chart()
        activity()
