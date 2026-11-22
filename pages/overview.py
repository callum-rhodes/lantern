"""Overview page: summaries, charts and a month x category table.

All aggregation is done in SQL (see the aggregation functions in ``db.py``) in
base-currency minor units; values are converted to the display currency only
when they are put into cards, charts and the table.
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Any

import plotly.graph_objects as go
from nicegui import app, ui

import components
import db
import state

INCOME_COLOR = "#21ba45"
EXPENSE_COLOR = "#c10015"
NET_COLOR = "#1976d2"
MONTH_NAMES = list(calendar.month_abbr)[1:]  # Jan ... Dec
HOVER = "%{x}: %{y:,.2f}<extra>%{fullData.name}</extra>"


# --------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------

def _months(year: int) -> list[str]:
    """The twelve 'YYYY-MM' keys of a year."""
    return [f"{year}-{month:02d}" for month in range(1, 13)]


def _pivot(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Turn (month, label, cents) rows into {label: {month: cents}}, biggest label first."""
    table: dict[str, dict[str, int]] = {}
    for row in rows:
        table.setdefault(row["label"], {})[row["month"]] = row["cents"]
    return dict(sorted(table.items(), key=lambda item: (-sum(item[1].values()), item[0])))


def _in_category_order(
    pivot: dict[str, dict[str, int]], category_options: dict[int, str]
) -> dict[str, dict[str, int]]:
    """Reorder pivot rows to match the category order set on the Categories page.

    ``category_options`` (from ``db.category_options``) lists each category followed
    by its subcategories, e.g. 'Food', 'Food > Groceries', 'Food > Restaurants', so
    its order is the user's own order and keeps subcategories grouped under their parent.
    Labels not found there (should not happen) go last.
    """
    position = {label: i for i, label in enumerate(category_options.values())}
    return dict(sorted(pivot.items(), key=lambda item: position.get(item[0], len(position))))


# Per-row diverging scale (RGB): green = good, white = the row's average, red = bad.
GREEN = (99, 190, 123)
NEUTRAL = (255, 255, 255)
RED = (248, 105, 107)


def _row_colours(values: list[int], reverse: bool) -> list[str]:
    """CSS background colour per value, diverging around the row's mean.

    Below-average values are green and above-average values red (swapped when
    ``reverse`` is set, e.g. for income). Intensity grows with the distance from
    the mean relative to the largest deviation in the row, so every row uses the
    full colour range. A row with no variation gets the neutral colour throughout.
    """
    if not values:
        return []
    mean = sum(values) / len(values)
    max_dev = max(abs(v - mean) for v in values)
    below, above = (RED, GREEN) if reverse else (GREEN, RED)
    colours = []
    for value in values:
        if max_dev == 0:
            colours.append("rgb({}, {}, {})".format(*NEUTRAL))
            continue
        t = (value - mean) / max_dev  # -1 .. +1
        target = above if t > 0 else below
        mixed = [round(n + (c - n) * abs(t)) for n, c in zip(NEUTRAL, target)]
        colours.append("rgb({}, {}, {})".format(*mixed))
    return colours


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------

def _style(fig: go.Figure, display: state.Display) -> go.Figure:
    """Common look: theme-aware, transparent background, amounts axis title."""
    dark = bool(app.storage.user.get("dark_mode"))
    fig.update_layout(
        template="plotly_dark" if dark else "plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=380,
        margin={"l": 70, "r": 20, "t": 20, "b": 40},
        yaxis_title=f"Amount ({display.code})",
        legend={"orientation": "h", "y": -0.15},
    )
    return fig


def _monthly_figure(pivot: dict[str, dict[str, int]], year: int, display: state.Display) -> go.Figure:
    """Stacked bars: one bar per month, one segment per category."""
    months = _months(year)
    fig = go.Figure()
    for label, by_month in pivot.items():
        fig.add_bar(
            name=label,
            x=MONTH_NAMES,
            y=[display.major(by_month.get(month, 0)) for month in months],
            hovertemplate=HOVER,
        )
    fig.update_layout(barmode="stack")
    return _style(fig, display)


def _income_expense_figure(rows: list[dict[str, Any]], year: int, display: state.Display) -> go.Figure:
    """Grouped bars for income and expenses with a net line."""
    by_month = {row["month"]: row for row in rows}
    months = _months(year)
    income = [by_month.get(m, {}).get("income", 0) for m in months]
    expenses = [by_month.get(m, {}).get("expenses", 0) for m in months]
    fig = go.Figure()
    fig.add_bar(name="Income", x=MONTH_NAMES, y=[display.major(v) for v in income],
                marker_color=INCOME_COLOR, hovertemplate=HOVER)
    fig.add_bar(name="Expenses", x=MONTH_NAMES, y=[display.major(v) for v in expenses],
                marker_color=EXPENSE_COLOR, hovertemplate=HOVER)
    fig.add_scatter(name="Net", x=MONTH_NAMES, mode="lines+markers",
                    y=[display.major(i - e) for i, e in zip(income, expenses)],
                    line={"color": NET_COLOR}, hovertemplate=HOVER)
    fig.update_layout(barmode="group")
    return _style(fig, display)


def _yearly_figure(
    rows: list[dict[str, Any]], display: state.Display, trend: tuple[str, list[dict[str, Any]]] | None
) -> go.Figure:
    """Lines across all years for income, expenses and net, plus an optional category line."""
    years = [str(row["year"]) for row in rows]
    fig = go.Figure()
    for name, color, values in (
        ("Income", INCOME_COLOR, [r["income"] for r in rows]),
        ("Expenses", EXPENSE_COLOR, [r["expenses"] for r in rows]),
        ("Net", NET_COLOR, [r["income"] - r["expenses"] for r in rows]),
    ):
        fig.add_scatter(name=name, x=years, y=[display.major(v) for v in values],
                        mode="lines+markers", line={"color": color}, hovertemplate=HOVER)
    if trend:
        name, trend_rows = trend
        fig.add_scatter(name=name, x=[str(r["year"]) for r in trend_rows],
                        y=[display.major(r["value"]) for r in trend_rows],
                        mode="lines+markers", line={"dash": "dash"}, hovertemplate=HOVER)
    fig.update_xaxes(type="category")
    return _style(fig, display)


# --------------------------------------------------------------------------
# Page pieces
# --------------------------------------------------------------------------

def _summary_cards(summary: dict[str, Any], display: state.Display) -> None:
    """Total income, total expenses, net and savings rate."""
    rate = summary["savings_rate"]
    cards = [
        ("Total income", display.money(summary["income"]), "text-positive"),
        ("Total expenses", display.money(summary["expenses"]), "text-negative"),
        ("Net", display.money(summary["net"], show_plus=True), components.amount_class(summary["net"])),
        ("Savings rate", "n/a" if rate is None else f"{rate * 100:.1f}%",
         "" if rate is None else components.amount_class(rate)),
    ]
    with ui.row().classes("w-full gap-4"):
        for title, text, css in cards:
            with ui.card().classes("grow"):
                ui.label(title).classes("text-caption text-grey-7")
                ui.label(text).classes(f"text-h5 {css}")


def _month_table(pivot: dict[str, dict[str, int]], year: int, display: state.Display, kind: str) -> None:
    """Month x category totals, with a total column and a total row.

    Each category row is colour-scaled around its own monthly average: green for
    low and red for high expenses (reversed for income). The Total column is scaled
    down the column (across categories) and the Total row across the months, with a
    bold divider above it. The bottom-right grand total is left uncoloured.
    """
    months = _months(year)

    def fmt(cents: int) -> str:
        return display.money(cents) if cents else "–"

    # Only colour months that have happened, so empty future months in the current
    # year don't drag the row average down.
    today = date.today()
    this_month = f"{today.year}-{today.month:02d}"
    scaled = [m for m in months if year != today.year or m <= this_month]

    columns = [{"name": "label", "label": "Category", "field": "label", "align": "left"}]
    columns += [{"name": m, "label": name, "field": m, "align": "right"} for m, name in zip(months, MONTH_NAMES)]
    columns.append({"name": "total", "label": "Total", "field": "total", "align": "right"})

    reverse = kind == "income"
    rows = []
    for label, by_month in pivot.items():
        row = {"label": label, **{m: fmt(by_month.get(m, 0)) for m in months}, "total": fmt(sum(by_month.values()))}
        colours = _row_colours([by_month.get(m, 0) for m in scaled], reverse)
        row.update({f"_bg_{m}": colour for m, colour in zip(scaled, colours)})
        rows.append(row)

    # Total column: scaled vertically, across the category rows.
    for row, colour in zip(rows, _row_colours([sum(by.values()) for by in pivot.values()], reverse)):
        row["_bg_total"] = colour

    # Total row: scaled across the months, and flagged so it gets a bold divider.
    month_totals = {m: sum(by.get(m, 0) for by in pivot.values()) for m in months}
    total_row = {
        "label": "Total", **{m: fmt(month_totals[m]) for m in months},
        "total": fmt(sum(month_totals.values())), "_is_total": True,
    }
    total_colours = _row_colours([month_totals[m] for m in scaled], reverse)
    total_row.update({f"_bg_{m}": colour for m, colour in zip(scaled, total_colours)})
    rows.append(total_row)

    table = ui.table(columns=columns, rows=rows, row_key="label", pagination=0)
    table.props("flat bordered dense hide-bottom").classes("w-full")
    # Per-cell background (black text keeps coloured cells readable in dark mode); the
    # total row also gets a bold top border and bold text so it reads as separate.
    table.add_slot("body-cell", r"""
        <q-td :props="props"
              :style="[
                  props.row['_bg_' + props.col.name]
                      ? {backgroundColor: props.row['_bg_' + props.col.name], color: '#000'}
                      : {},
                  props.row._is_total
                      ? {borderTop: '3px solid #757575', fontWeight: 'bold'}
                      : {},
              ]">
            {{ props.value }}
        </q-td>
    """)


@ui.page("/overview")
async def overview_page() -> None:
    """Render the overview page."""
    ctx = await components.build_layout("Overview")
    if ctx is None:
        return
    user_id, display = ctx.user["id"], ctx.display
    years = db.available_years(user_id)

    with components.content():
        ui.label("Overview").classes("text-h5")
        if not years:
            components.info_message("No data yet. Add some transactions to see charts here.", "insights")
            return

        category_options = db.category_options(user_id)
        label_to_id = {label: cid for cid, label in category_options.items()}
        current_year = date.today().year

        @ui.refreshable
        def dashboard() -> None:
            year, category_id, level = year_select.value, category_filter.value, level_toggle.value
            kind = kind_toggle.value
            kind_word = "spending" if kind == "expense" else "income"

            _summary_cards(db.period_summary(user_id, year, category_id), display)

            monthly_rows = db.monthly_by_category(user_id, year, kind, level, category_id)
            pivot = _pivot(monthly_rows)

            # Month x category table (colour-scaled per row)
            ui.label(f"Month x category totals ({kind_word})").classes("text-h6")
            if not pivot:
                components.info_message("Nothing to show for this selection.", "table_chart")
            else:
                ui.label("Colours are relative to each row's own monthly average.").classes("text-caption text-grey-7")
                _month_table(_in_category_order(pivot, category_options), year, display, kind)

            # Monthly breakdown (stacked bars with drill-down)
            ui.label(f"Monthly {kind_word} by {level}").classes("text-h6")
            if not pivot:
                components.info_message(f"No {kind} data for {year} with this selection.", "bar_chart")
            else:
                fig = _monthly_figure(pivot, year, display)
                ui.label("Click a bar segment to drill into that category.").classes("text-caption text-grey-7")
                chart = ui.plotly(fig).classes("w-full")

                def drill_down(event: Any) -> None:
                    points = (event.args or {}).get("points") or []
                    if not points:
                        return
                    selected = label_to_id.get(fig.data[points[0]["curveNumber"]].name)
                    if selected is not None:
                        level_toggle.value = "subcategory"
                        category_filter.value = selected

                chart.on("plotly_click", drill_down)

            # Income vs expenses by month
            ui.label("Income vs expenses by month").classes("text-h6")
            ie_rows = db.monthly_income_expense(user_id, year, category_id)
            if not ie_rows:
                components.info_message(f"No data for {year} with this selection.", "bar_chart")
            else:
                ui.plotly(_income_expense_figure(ie_rows, year, display)).classes("w-full")

            # Yearly trends
            ui.label("Yearly trends").classes("text-h6")
            trend = None
            if trend_select.value is not None:
                trend_cat = db.get_category(user_id, trend_select.value)
                if trend_cat:
                    field = "expenses" if trend_cat["kind"] == "expense" else "income"
                    trend_rows = [
                        {"year": r["year"], "value": r[field]}
                        for r in db.yearly_totals(user_id, trend_select.value)
                    ]
                    trend = (f"{category_options[trend_cat['id']]} ({field})", trend_rows)
            ui.plotly(_yearly_figure(db.yearly_totals(user_id), display, trend)).classes("w-full")

        # Controls
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-4"):
                year_select = ui.select(
                    {y: str(y) for y in years}, label="Year",
                    value=current_year if current_year in years else years[-1],
                    on_change=lambda: dashboard.refresh(),
                ).classes("w-28")
                category_filter = ui.select(
                    category_options, label="Category filter", clearable=True, with_input=True,
                    on_change=lambda: dashboard.refresh(),
                ).classes("w-56")
                level_toggle = ui.toggle(
                    {"category": "Category", "subcategory": "Subcategory"}, value="category",
                    on_change=lambda: dashboard.refresh(),
                )
                kind_toggle = ui.toggle(
                    {"expense": "Expenses", "income": "Income"}, value="expense",
                    on_change=lambda: dashboard.refresh(),
                )
                trend_select = ui.select(
                    category_options, label="Add category trend line", clearable=True, with_input=True,
                    on_change=lambda: dashboard.refresh(),
                ).classes("w-56")

        dashboard()
