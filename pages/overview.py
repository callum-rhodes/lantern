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
from db import reports, categories
import state

INCOME_COLOR = "#21ba45"
EXPENSE_COLOR = "#c10015"
NET_COLOR = "#1976d2"
MONTH_NAMES = list(calendar.month_abbr)[1:]  # Jan ... Dec
HOVER = "%{x}: %{y:,.2f}<extra>%{fullData.name}</extra>"


# --------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------

ALL_YEARS = 0  # value of the "All years" entry in the year dropdown

def _periods(year: int, years: list[int]) -> tuple[list[str], list[str]]:
    """(keys, labels) for the columns: 12 months of a year, or every year from first to last."""
    if year == ALL_YEARS:
        keys = [str(y) for y in range(years[0], years[-1] + 1)]
        return keys, keys
    return [f"{year}-{m:02d}" for m in range(1, 13)], MONTH_NAMES


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

    ``category_options`` (from ``db/categories.category_options``) lists each category followed
    by its subcategories, e.g. 'Food', 'Food > Groceries', 'Food > Restaurants', so
    its order is the user's own order and keeps subcategories grouped under their parent.
    Labels not found there (should not happen) go last.
    """
    position = {label: i for i, label in enumerate(category_options.values())}
    return dict(sorted(pivot.items(), key=lambda item: position.get(item[0], len(position))))

def _completed_months(year: int, years: list[int]) -> list[str]:
    """Months ('YYYY-MM') that have fully finished, within the selection."""
    today = date.today()
    this_month = f"{today.year}-{today.month:02d}"
    if year == ALL_YEARS:
        keys = [f"{y}-{m:02d}" for y in range(years[0], years[-1] + 1) for m in range(1, 13)]
    else:
        keys = [f"{year}-{m:02d}" for m in range(1, 13)]
    return [k for k in keys if k < this_month]


def _averages(cat_rows, ie_rows, completed: list[str]) -> dict[str, Any] | None:
    """Per-month averages over completed months, from the first month with data."""
    if not ie_rows:
        return None
    first = min(r["month"] for r in ie_rows)
    completed = [m for m in completed if m >= first]
    if not completed:
        return None
    done, n = set(completed), len(completed)
    cats: dict[str, float] = {}
    for r in cat_rows:
        if r["month"] in done:
            cats[r["label"]] = cats.get(r["label"], 0) + r["cents"]
    income = sum(r["income"] for r in ie_rows if r["month"] in done)
    expenses = sum(r["expenses"] for r in ie_rows if r["month"] in done)
    return {
        "cats": {label: v / n for label, v in cats.items()},
        "income": income / n,
        "expenses": expenses / n,
        "net": (income - expenses) / n,
    }

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

def _add_average_line(fig, labels, display, cents, name, colour, factor=1) -> None:
    """Flat dashed line at an average value (skipped when there is no average yet)."""
    if cents is None:
        return
    fig.add_scatter(
        name=name, x=labels, y=[display.major(cents * factor)] * len(labels),
        mode="lines", line={"color": colour, "dash": "dash"}, hovertemplate=HOVER,
    )

AVG_EXPENSE_COLOR = "#ff9800"

def _monthly_figure(pivot, periods, display, averages=None, kind="expense", all_years=False) -> go.Figure:
    keys, labels = periods
    fig = go.Figure()
    for label, by_period in pivot.items():
        fig.add_bar(
            name=label, x=labels,
            y=[display.major(by_period.get(k, 0)) for k in keys],
            hovertemplate=HOVER,
        )
    fig.update_layout(barmode="stack")
    if averages:
        word = "expenses" if kind == "expense" else "income"
        _add_average_line(
            fig, labels, display, averages[word],
            f"Avg {word} / {'year' if all_years else 'month'}", AVG_EXPENSE_COLOR,
            factor=12 if all_years else 1,
        )
    return _style(fig, display)


def _income_expense_figure(rows, periods, display, averages=None, all_years=False) -> go.Figure:
    keys, labels = periods
    by_period = {row["month"]: row for row in rows}
    income = [by_period.get(k, {}).get("income", 0) for k in keys]
    expenses = [by_period.get(k, {}).get("expenses", 0) for k in keys]
    fig = go.Figure()
    fig.add_bar(name="Income", x=labels, y=[display.major(v) for v in income],
                marker_color=INCOME_COLOR, hovertemplate=HOVER)
    fig.add_bar(name="Expenses", x=labels, y=[display.major(v) for v in expenses],
                marker_color=EXPENSE_COLOR, hovertemplate=HOVER)
    fig.add_scatter(name="Net", x=labels, mode="lines+markers",
                    y=[display.major(i - e) for i, e in zip(income, expenses)],
                    line={"color": NET_COLOR}, hovertemplate=HOVER)
    fig.update_layout(barmode="group")
    if averages:
        _add_average_line(
            fig, labels, display, averages["net"],
            f"Avg net / {'year' if all_years else 'month'}", NET_COLOR,
            factor=12 if all_years else 1,
        )
    return _style(fig, display)


PIE_OTHER_THRESHOLD = 0.03  # slices below this share are merged into "Other"

def _pie_figure(pivot: dict[str, dict[str, int]], display: state.Display) -> go.Figure:
    """Pie of each category's share of the total; small slices are merged into 'Other'."""
    totals = {label: sum(by_month.values()) for label, by_month in pivot.items()}
    totals = {label: cents for label, cents in totals.items() if cents > 0}
    grand_total = sum(totals.values())

    small = [label for label, cents in totals.items() if cents / grand_total < PIE_OTHER_THRESHOLD]
    if len(small) > 1:  # merging a single slice into "Other" would just rename it
        other = sum(totals.pop(label) for label in small)
        totals["Other"] = other

    fig = go.Figure(go.Pie(
        labels=list(totals),
        values=[display.major(cents) for cents in totals.values()],
        sort=False,  # keep biggest-first order, with Other last
        textinfo="percent",
        hovertemplate="%{label}: %{value:,.2f} (%{percent})<extra></extra>",
    ))
    _style(fig, display)
    fig.update_layout(height=650, margin={"l": 20, "r": 20, "t": 20, "b": 40})
    return fig


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


def _month_table(pivot, ie_rows, year, periods, display, kind, level="category", averages=None) -> None:
    """Month x category totals, laid out as Total income, Total expenses, categories, Net.

    Every row is colour-scaled around its own monthly average. Category rows follow
    ``kind`` (low spending is green, high income is green). In the summary rows,
    income and net are green when high and expenses are green when low. The summary
    rows always cover income and expenses together (respecting the category filter),
    whichever kind the category rows show. The Total column of the category rows is
    scaled down the column; the summary rows' grand totals are left uncoloured.
    """
    months, names = periods

    def fmt(cents: int) -> str:
        return display.money(cents) if cents else "–"

    # Only colour months that have happened, so empty future months in the current
    # year don't drag the row average down.
    today = date.today()
    this_month = f"{today.year}-{today.month:02d}"
    scaled = [m for m in months if year != today.year or m <= this_month]

    columns = [{"name": "label", "label": "Category", "field": "label", "align": "left"}]
    columns += [{"name": m, "label": name, "field": m, "align": "right"} for m, name in zip(months, names)]
    columns.append({"name": "total", "label": "Total", "field": "total", "align": "right",
                    "headerStyle": "border-left: 3px solid #757575"})
    columns.append({"name": "average", "label": "Avg / month", "field": "average", "align": "right"})

    reverse = kind == "income"

    # Category rows.
    category_rows = []
    previous_group = None
    for label, by_month in pivot.items():
        avg = averages["cats"].get(label, 0) if averages else 0
        row = {"label": label, **{m: fmt(by_month.get(m, 0)) for m in months},
               "total": fmt(sum(by_month.values())), "average": fmt(avg)}
        colours = _row_colours([by_month.get(m, 0) for m in scaled], reverse)
        row.update({f"_bg_{m}": colour for m, colour in zip(scaled, colours)})
        group = label.split(" > ")[0]  # 'Food > Groceries' -> 'Food'
        if level == "subcategory" and previous_group is not None and group != previous_group:
            row["_group_start"] = True
        previous_group = group
        category_rows.append(row)

    # Total column of the category rows: scaled vertically, across the categories.
    for row, colour in zip(category_rows, _row_colours([sum(by.values()) for by in pivot.values()], reverse)):
        row["_bg_total"] = colour
    if averages:
        avg_values = [averages["cats"].get(label, 0) for label in pivot]
        for row, colour in zip(category_rows, _row_colours(avg_values, reverse)):
            row["_bg_average"] = colour

    # Summary rows (always both kinds).
    by_month = {r["month"]: r for r in ie_rows}
    income = {m: by_month.get(m, {}).get("income", 0) for m in months}
    expenses = {m: by_month.get(m, {}).get("expenses", 0) for m in months}
    net = {m: income[m] - expenses[m] for m in months}

    def summary_row(label: str, values: dict[str, int], reverse_row: bool, avg: float, **flags: bool) -> dict[str, Any]:
        row = {
            "label": label, **{m: fmt(values[m]) for m in months},
            "total": fmt(sum(values.values())), "average": fmt(avg),
            "_is_total": True, **flags,
        }
        colours = _row_colours([values[m] for m in scaled], reverse_row)
        row.update({f"_bg_{m}": colour for m, colour in zip(scaled, colours)})
        return row

    a = averages or {"income": 0, "expenses": 0, "net": 0}
    rows = [
        summary_row("Total income", income, True, a["income"]),
        summary_row("Total expenses", expenses, False, a["expenses"], _divider_below=True),
        *category_rows,
        summary_row("Net", net, True, a["net"], _divider_above=True),
    ]

    table = ui.table(columns=columns, rows=rows, row_key="label", pagination=0)
    table.props("flat bordered dense hide-bottom").classes("w-full")
    # Per-cell background (black text keeps coloured cells readable in dark mode).
    # Summary rows are bold, with a thick divider under the expenses row and above Net.
    table.add_slot("body-cell", r"""
        <q-td :props="props"
              :style="[
                  props.row['_bg_' + props.col.name]
                      ? {backgroundColor: props.row['_bg_' + props.col.name], color: '#000'}
                      : {},
                  props.row._is_total ? {fontWeight: 'bold'} : {},
                  props.row._divider_below ? {borderBottom: '3px solid #757575'} : {},
                  props.row._divider_above ? {borderTop: '3px solid #757575'} : {},
                  props.row._group_start ? {borderTop: '2px solid #9e9e9e'} : {},
                  props.col.name === 'total' ? {borderLeft: '3px solid #757575'} : {},
              ]">
            {{ props.value }}
        </q-td>
    """)


@ui.page("/overview")
@ui.page("/")
async def overview_page() -> None:
    """Render the overview page."""
    ctx = await components.build_layout("Overview")
    if ctx is None:
        return
    user_id, display = ctx.user["id"], ctx.display
    years = reports.available_years(user_id)

    with components.content():
        ui.label("Overview").classes("text-h5")
        if not years:
            components.info_message("No data yet. Add some transactions to see charts here.", "insights")
            return

        category_options = categories.category_options(user_id)
        label_to_id = {label: cid for cid, label in category_options.items()}
        current_year = date.today().year

        @ui.refreshable
        def dashboard() -> None:
            year, category_id, level = year_select.value, category_filter.value, level_toggle.value
            kind = kind_toggle.value
            kind_word = "spending" if kind == "expense" else "income"
            all_years = year == ALL_YEARS
            period_word = "Yearly" if all_years else "Monthly"
            year_text = "all years" if all_years else str(year)
            periods = _periods(year, years)

            _summary_cards(reports.period_summary(user_id, None if all_years else year, category_id), display)

            if all_years:
                monthly_rows = reports.yearly_by_category(user_id, kind, level, category_id)
                ie_rows = [
                    {"month": str(r["year"]), "income": r["income"], "expenses": r["expenses"]}
                    for r in reports.yearly_totals(user_id, category_id)
                ]
                month_cat, month_ie = [], []
                for y in years:
                    month_cat += reports.monthly_by_category(user_id, y, kind, level, category_id)
                    month_ie += reports.monthly_income_expense(user_id, y, category_id)
            else:
                monthly_rows = reports.monthly_by_category(user_id, year, kind, level, category_id)
                ie_rows = reports.monthly_income_expense(user_id, year, category_id)
                month_cat, month_ie = monthly_rows, ie_rows
            
            pivot = _pivot(monthly_rows)

            averages = _averages(month_cat, month_ie, _completed_months(year, years))

            ui.label(f"{period_word} totals by category ({kind_word})").classes("text-h6")
            if not pivot:
                components.info_message("Nothing to show for this selection.", "table_chart")
            else:
                ui.label("Colours are relative to each row's own average.").classes("text-caption text-grey-7")
                _month_table(_in_category_order(pivot, category_options), ie_rows, year, periods, display, kind, level, averages)

            ui.label(f"{period_word} {kind_word} by {level}").classes("text-h6")
            if not pivot:
                components.info_message(f"No {kind} data for {year_text} with this selection.", "bar_chart")
            else:
                fig = _monthly_figure(pivot, periods, display, averages, kind, all_years)
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

            # Income vs expenses by month/year
            ui.label(f"Income vs expenses by {'year' if all_years else 'month'}").classes("text-h6")
            if not ie_rows:
                components.info_message(f"No data for {year_text} with this selection.", "bar_chart")
            else:
                ui.plotly(_income_expense_figure(ie_rows, periods, display, averages, all_years)).classes("w-full")

            # Share of the total (pie with drill-down)
            ui.label(f"{kind_word.capitalize()} share by {level}, {year_text}").classes("text-h6")
            if not pivot:
                components.info_message(f"No {kind} data for {year_text} with this selection.", "pie_chart")
            else:
                ui.label("Click a slice to drill into that category.").classes("text-caption text-grey-7")
                pie = ui.plotly(_pie_figure(pivot, display)).classes("w-full")

                def pie_drill_down(event: Any) -> None:
                    points = (event.args or {}).get("points") or []
                    if not points:
                        return
                    selected = label_to_id.get(points[0].get("label"))
                    if selected is not None:  # "Other" has no category, so it does nothing
                        level_toggle.value = "subcategory"
                        category_filter.value = selected

                pie.on("plotly_click", pie_drill_down)

        # Controls
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-4"):
                year_select = ui.select(
                    {ALL_YEARS: "All years", **{y: str(y) for y in years}}, label="Year",
                    value=current_year if current_year in years else years[-1],
                    on_change=lambda: dashboard.refresh(),
                ).classes("w-32")
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
                # Existed in the old spending trend graph, got rid of but might be useful later.
                # trend_select = ui.select(
                #     category_options, label="Add category trend line", clearable=True, with_input=True,
                #     on_change=lambda: dashboard.refresh(),
                # ).classes("w-56")

        dashboard()
