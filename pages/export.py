"""Export CSV page: download transactions and/or month x category totals as CSV.

Amounts are written as plain signed numbers (no currency symbol) so they open
cleanly in a spreadsheet. Everything is read through the existing ``db`` functions.
"""
from __future__ import annotations

import calendar
import csv
import io
from datetime import date

from nicegui import ui

import components
from db import DataError, transactions, reports, categories
import rates


def _money(cents: int, rate: float) -> str:
    """Plain number with two decimals, e.g. '-12.50'."""
    return f"{rates.to_major(cents, rate):.2f}"


def transactions_csv(user_id: int, rate: float) -> str:
    """Every transaction, oldest first, one row each."""
    txns = sorted(transactions.list_transactions(user_id), key=lambda t: (t["date"], t["id"]))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date", "Description", "Category", "Subcategory", "Type", "Amount"])
    for t in txns:
        top = t["parent_name"] or t["category_name"]
        sub = t["category_name"] if t["parent_name"] else ""
        writer.writerow([t["date"], t["description"], top, sub, t["kind"],
                         _money(t["amount_cents"], rate)])
    return out.getvalue()


def totals_csv(user_id: int, level: str, rate: float) -> str:
    """Month x category totals for all years side by side (expenses, then income).

    One row per category; one column per month from the first to the last year
    with data, then a Total column. Expenses are positive, as on the Overview page.
    """
    years = reports.available_years(user_id)
    months = [f"{y}-{m:02d}" for y in range(years[0], years[-1] + 1) for m in range(1, 13)] if years else []
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Type", "Category"]
                    + [f"{calendar.month_abbr[int(m[5:])]} {m[:4]}" for m in months] + ["Total"])

    # Same order as the Categories page (subcategories grouped under their parent).
    order = {label: i for i, label in enumerate(categories.category_options(user_id).values())}

    for kind in ("expense", "income"):
        pivot: dict[str, dict[str, int]] = {}
        for year in years:
            for row in reports.monthly_by_category(user_id, year, kind, level):
                pivot.setdefault(row["label"], {})[row["month"]] = row["cents"]
        for label in sorted(pivot, key=lambda l: (order.get(l, len(order)), l)):
            by_month = pivot[label]
            writer.writerow([kind, label]
                            + [_money(by_month.get(m, 0), rate) for m in months]
                            + [_money(sum(by_month.values()), rate)])
        if pivot:
            month_totals = [sum(by.get(m, 0) for by in pivot.values()) for m in months]
            writer.writerow([kind, "Total"] + [_money(v, rate) for v in month_totals]
                            + [_money(sum(month_totals), rate)])
    return out.getvalue()


def _download(text: str, filename: str) -> None:
    """Send a CSV to the browser (UTF-8 with BOM so Excel reads accents correctly)."""
    ui.download.content(text.encode("utf-8-sig"), filename, "text/csv")


@ui.page("/export")
async def export_page() -> None:
    """Render the export page."""
    ctx = await components.build_layout("Export CSV")
    if ctx is None:
        return
    user_id = ctx.user["id"]

    def export() -> None:
        if not (want_transactions.value or want_totals.value):
            ui.notify("Tick at least one export.", type="warning")
            return
        if transactions.transaction_count(user_id) == 0:
            ui.notify("There are no transactions to export yet.", type="warning")
            return
        rate = 1.0 if amounts.value == "base" else ctx.display.rate
        stamp = date.today().isoformat()
        name = ctx.user["name"].replace(" ", "_")
        try:
            if want_transactions.value:
                _download(transactions_csv(user_id, rate), f"transactions_{name}_{stamp}.csv")
            if want_totals.value:
                _download(totals_csv(user_id, level.value, rate),
                          f"month_category_totals_{name}_{stamp}.csv")
        except DataError as exc:
            components.notify_error(exc)

    with components.content():
        ui.label("Export CSV").classes("text-h5")
        with ui.card().classes("w-full gap-2"):
            ui.label("What to export").classes("text-subtitle1 text-bold")
            want_transactions = ui.checkbox("Transactions (all, one row each)", value=True)
            want_totals = ui.checkbox("Month x category totals (all years side by side)", value=True)

            ui.label("Options").classes("text-subtitle1 text-bold q-mt-md")
            with ui.row().classes("items-center gap-6"):
                amounts = ui.toggle(
                    {"base": f"Base currency ({ctx.base_currency})",
                     "display": f"Display currency ({ctx.display.code})"},
                    value="base",
                )
                level = ui.toggle(
                    {"subcategory": "Subcategory rows", "category": "Category rows"},
                    value="subcategory",
                ).bind_visibility_from(want_totals, "value")
            ui.label("If both are ticked, two files download. Your browser may ask to allow that.") \
                .classes("text-caption text-grey-7")
            ui.button("Export", icon="download", on_click=export).classes("q-mt-sm")