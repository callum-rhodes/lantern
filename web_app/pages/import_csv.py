"""Import page: load a bank CSV, auto-assign categories, ask about the rest, skip duplicates.

Flow: choose a file -> map its columns -> preview -> import.

* Amounts in the file are taken to be in the base currency (like the entry form).
  Positive = income, negative = spending (use "Flip sign" if your bank does it the other way).
* Categories are matched from (1) an optional category column in the file, then
  (2) descriptions you have already used: exact match first, then a known description
  appearing inside the new one (e.g. 'Tesco' inside 'TESCO STORES 3214').
* Rows with no match are listed so you can pick a category for each (or tick Skip).
* "Skip existing" ignores rows whose date, amount and category match a transaction
  already in the database. Rows within the file never count as duplicates of each other.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nicegui import ui

from web_app import components
from db import DataError, transactions, categories, rates

DATE_FORMATS = {
    "%Y-%m-%d": "2026-03-31",
    "%d/%m/%Y": "31/03/2026",
    "%d-%m-%Y": "31-03-2026",
    "%d/%m/%y": "31/03/26",
    "%m/%d/%Y": "03/31/2026 (US)",
    "%d %b %Y": "31 Mar 2026",
}

COLUMN_GUESSES = {
    "date": ("date", "transaction date", "posting date", "posted"),
    "amount": ("amount", "value", "amount (gbp)"),
    "description": ("description", "details", "memo", "narrative", "payee", "reference", "name"),
    "category": ("category",),
}

AMOUNT_SLOT = (
    '<q-td :props="props" class="text-right">'
    '<span :class="props.row.amount_class">{{ props.value }}</span></q-td>'
)

TABLE_COLUMNS = [
    {"name": "date", "label": "Date", "field": "date", "align": "left"},
    {"name": "description", "label": "Description", "field": "description", "align": "left"},
    {"name": "category", "label": "Category", "field": "category", "align": "left"},
    {"name": "amount", "label": "Amount", "field": "amount", "align": "right"},
    {"name": "status", "label": "", "field": "status", "align": "left"},
]


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Lower-case with collapsed whitespace, for comparing descriptions and names."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def parse_csv(raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Decode and read a CSV file into (headers, rows). Raises ``DataError`` if unusable."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    table = [row for row in reader if any(cell.strip() for cell in row)]
    if len(table) < 2:
        raise DataError("The file needs a header row and at least one data row.")
    headers = [h.strip() or f"Column {i + 1}" for i, h in enumerate(table[0])]
    rows = [dict(zip(headers, (cell.strip() for cell in row))) for row in table[1:]]
    return headers, rows


def _guess_column(headers: list[str], kind: str) -> str | None:
    """Pick the header that most likely holds ``kind`` (exact name first, then 'contains')."""
    lowered = {h.lower(): h for h in headers}
    names = COLUMN_GUESSES[kind]
    for name in names:
        if name in lowered:
            return lowered[name]
    for name in names:
        for low, original in lowered.items():
            if name in low:
                return original
    return None


def _guess_date_format(values: list[str]) -> str:
    """First date format that reads every sample value (day-first before month-first)."""
    sample = [v for v in values if v][:50]
    for fmt in DATE_FORMATS:
        try:
            for value in sample:
                datetime.strptime(value, fmt)
        except ValueError:
            continue
        return fmt
    return "%d/%m/%Y"


def _parse_amount(text: str) -> int:
    """'£1,234.50', '(12.50)', '-12,50' ... -> signed minor units."""
    s = (text or "").strip()
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d.,\-+]", "", s)  # drop currency symbols and spaces
    if "," in s and "." not in s and re.search(r",\d{2}$", s):
        s = s.replace(",", ".")  # European decimal comma
    if negative:
        s = "-" + s.lstrip("-")
    if s in ("", "-", "+"):
        raise DataError("no amount")
    return rates.amount_to_cents(s)


# --------------------------------------------------------------------------
# Category matching
# --------------------------------------------------------------------------

class Matcher:
    """Finds a category for a row from earlier transactions and category names."""

    def __init__(self, user_id: int) -> None:
        self.by_description = transactions.description_category_map(user_id)  # {(desc, kind): id}
        self.by_label: dict[tuple[str, str], int] = {}
        for cat in categories.list_categories(user_id):  # top-level first, so plain names favour them
            label = f"{cat['parent_name']} > {cat['name']}" if cat["parent_name"] else cat["name"]
            self.by_label[(_norm(label), cat["kind"])] = cat["id"]
            if not categories.is_other(cat):
                self.by_label.setdefault((_norm(cat["name"]), cat["kind"]), cat["id"])

    def find(self, description: str, kind: str, csv_category: str = "") -> int | None:
        """Category id for this row, or None if nothing matches."""
        if csv_category:
            found = self.by_label.get((_norm(csv_category), kind))
            if found:
                return found
        text = _norm(description)
        if not text:
            return None
        exact = self.by_description.get((text, kind))
        if exact:
            return exact
        best: tuple[int, int] | None = None  # (length of known description, category id)
        for (known, known_kind), category_id in self.by_description.items():
            if known_kind == kind and len(known) >= 4 and known in text:
                if best is None or len(known) > best[0]:
                    best = (len(known), category_id)
        return best[1] if best else None


@dataclass
class ImportRow:
    """One CSV row ready to import, with the widgets used to pick its category."""

    line: int
    date: str
    cents: int
    description: str
    kind: str
    category_id: int | None
    select: Any = None
    skip: Any = None


def prepare_rows(
    user_id: int, csv_rows: list[dict[str, str]], date_col: str, amount_col: str,
    desc_col: str, cat_col: str, date_fmt: str, invert: bool,
) -> tuple[list[ImportRow], list[str]]:
    """Turn raw CSV rows into ImportRows; also return a message per unreadable row."""
    matcher = Matcher(user_id)
    rows: list[ImportRow] = []
    problems: list[str] = []
    for line, raw in enumerate(csv_rows, start=2):  # line 1 is the header
        date_text = raw.get(date_col, "")
        try:
            day = datetime.strptime(date_text, date_fmt).date().isoformat()
        except ValueError:
            problems.append(f"Line {line}: unreadable date '{date_text}'")
            continue
        try:
            cents = _parse_amount(raw.get(amount_col, ""))
        except DataError:
            problems.append(f"Line {line}: unreadable amount '{raw.get(amount_col, '')}'")
            continue
        if invert:
            cents = -cents
        if cents == 0:
            problems.append(f"Line {line}: zero amount")
            continue
        description = raw.get(desc_col, "") if desc_col else ""
        kind = "income" if cents > 0 else "expense"
        category_id = matcher.find(description, kind, raw.get(cat_col, "") if cat_col else "")
        rows.append(ImportRow(line, day, cents, description, kind, category_id))
    return rows, problems


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

@ui.page("/import")
async def import_page() -> None:
    """Render the CSV import page."""
    ctx = await components.build_layout("Import CSV")
    if ctx is None:
        return
    user_id = ctx.user["id"]

    def money(cents: int) -> str:
        """Amounts in the file are base currency, so show them as such (no conversion)."""
        return rates.format_money(cents, ctx.base_currency)

    # ---- step 3: preview and import ------------------------------------------------
    def show_preview(rows: list[ImportRow], problems: list[str], skip_dupes: ui.switch) -> None:
        preview_box.clear()
        with preview_box:
            if problems:
                with ui.expansion(f"{len(problems)} row(s) could not be read and will be ignored",
                                  icon="warning").classes("w-full"):
                    for message in problems[:100]:
                        ui.label(message).classes("text-caption")
            if not rows:
                components.info_message("No readable rows. Check the column choices above.", "error_outline")
                return

            auto = [r for r in rows if r.category_id is not None]
            manual = [r for r in rows if r.category_id is None]
            existing = transactions.existing_keys(user_id, min(r.date for r in rows), max(r.date for r in rows))
            others = transactions.other_subcategory_map(user_id)
            ui.label(
                f"{len(rows)} row(s) read: {len(auto)} matched automatically, "
                f"{len(manual)} need a category."
            ).classes("text-h6")

            if manual:
                options = {kind: categories.category_options(user_id, kind=kind) for kind in categories.KINDS}

                def propagate(source: ImportRow, value: Any) -> None:
                    """Reuse a choice for other empty rows with the same description."""
                    if value is None or not _norm(source.description):
                        return
                    for other in manual:
                        if (other is not source and other.select.value is None
                                and other.kind == source.kind
                                and _norm(other.description) == _norm(source.description)):
                            other.select.value = value

                with ui.card().classes("w-full"):
                    ui.label("Choose a category").classes("text-subtitle1 text-bold")
                    ui.label(
                        "No match was found for these. Pick a category for each, or tick Skip. "
                        "Choosing one also fills other empty rows with the same description."
                    ).classes("text-caption text-grey-7")
                    for row in manual:
                        with ui.row().classes("w-full items-center gap-4"):
                            ui.label(row.date).classes("w-24")
                            ui.label(row.description or "(no description)").classes("grow")
                            ui.label(money(row.cents)).classes(
                                f"w-28 text-right {components.amount_class(row.cents)}")
                            row.select = ui.select(
                                options[row.kind], label=f"{row.kind.title()} category",
                                with_input=True, clearable=True,
                                on_change=lambda e, r=row: propagate(r, e.value),
                            ).classes("w-72")
                            row.skip = ui.checkbox("Skip")

            if auto:
                labels = categories.category_options(user_id)
                ui.label("Matched automatically").classes("text-subtitle1 text-bold")
                table = ui.table(
                    columns=TABLE_COLUMNS, row_key="line", pagination=15,
                    rows=[{
                        "line": r.line, "date": r.date, "description": r.description,
                        "category": labels.get(others.get(r.category_id, r.category_id), "?"),
                        "amount_class": components.amount_class(r.cents),
                        "status": "Already exists"
                            if (r.date, r.cents, others.get(r.category_id, r.category_id)) in existing else "",
                    } for r in auto],
                )
                table.props("flat bordered dense").classes("w-full")
                table.add_slot("body-cell-amount", AMOUNT_SLOT)
                if any(row["status"] for row in table.rows):
                    ui.label("'Already exists' rows are skipped when the skip option above is on."
                             ).classes("text-caption text-grey-7")

            def do_import() -> None:
                chosen = list(auto)
                missing = 0
                for row in manual:
                    if row.skip.value:
                        continue
                    if row.select.value is None:
                        missing += 1
                        continue
                    row.category_id = row.select.value
                    chosen.append(row)
                if missing:
                    ui.notify(f"{missing} transaction(s) still need a category (or tick Skip).",
                              type="warning")
                    return
                if not chosen:
                    ui.notify("Nothing to import.", type="warning")
                    return
                chosen.sort(key=lambda r: r.line)
                try:
                    result = transactions.import_transactions(
                        user_id, [(r.date, r.cents, r.category_id, r.description) for r in chosen],
                        skip_duplicates=skip_dupes.value,
                    )
                except DataError as exc:
                    components.notify_error(exc)
                    return
                text = f"Imported {result['added']} transaction(s)"
                if result["skipped"]:
                    text += f", skipped {result['skipped']} that already existed"
                ui.notify(text + ".", type="positive")
                preview_box.clear()
                mapping_box.clear()

            ui.button("Import", icon="upload", on_click=do_import)

    # ---- step 2: column mapping ----------------------------------------------------
    def show_mapping(filename: str, headers: list[str], csv_rows: list[dict[str, str]]) -> None:
        mapping_box.clear()
        preview_box.clear()
        required = {h: h for h in headers}
        optional = {"": "(none)", **required}
        guessed_date = _guess_column(headers, "date")
        with mapping_box, ui.card().classes("w-full"):
            ui.label(f"{filename}: {len(csv_rows)} row(s)").classes("text-subtitle1 text-bold")
            with ui.row().classes("w-full items-center gap-4"):
                date_col = ui.select(required, value=guessed_date, label="Date column").classes("w-48")
                amount_col = ui.select(required, value=_guess_column(headers, "amount"),
                                       label="Amount column").classes("w-48")
                desc_col = ui.select(optional, value=_guess_column(headers, "description") or "",
                                     label="Description column").classes("w-48")
                cat_col = ui.select(optional, value=_guess_column(headers, "category") or "",
                                    label="Category column (optional)").classes("w-56")
            with ui.row().classes("w-full items-center gap-4"):
                date_fmt = ui.select(
                    DATE_FORMATS,
                    value=_guess_date_format([r.get(guessed_date or "", "") for r in csv_rows]),
                    label="Date format",
                ).classes("w-56")
                invert = ui.switch("Flip sign (positive amounts are spending)")
                skip_dupes = ui.switch(
                    "Skip transactions that already exist (same date, amount and category)", value=True)
            ui.label(f"Amounts are read as {ctx.base_currency}, your base currency.") \
                .classes("text-caption text-grey-7")

            def preview() -> None:
                if not date_col.value or not amount_col.value:
                    ui.notify("Choose the date and amount columns.", type="warning")
                    return
                rows, problems = prepare_rows(
                    user_id, csv_rows, date_col.value, amount_col.value, desc_col.value,
                    cat_col.value, date_fmt.value, invert.value,
                )
                show_preview(rows, problems, skip_dupes)

            ui.button("Preview", icon="preview", on_click=preview)

    # ---- step 1: file picker -------------------------------------------------------
    async def handle_upload(event: Any) -> None:
        try:
            headers, csv_rows = parse_csv(await event.file.read())
            name = event.file.name
        except DataError as exc:
            components.notify_error(exc)
            return
        finally:
            upload.reset()
        show_mapping(name, headers, csv_rows)

    with components.content():
        ui.label("Import from CSV").classes("text-h5")
        ui.label(
            "Upload a bank export. Categories are matched from your earlier transactions; "
            "anything unmatched is left for you to choose."
        ).classes("text-grey-7")
        with ui.card().classes("w-full"):
            upload = ui.upload(
                label="Choose a CSV file", auto_upload=True, max_files=1, on_upload=handle_upload,
            ).props("accept=.csv,.txt flat bordered").classes("w-full")
        mapping_box = ui.column().classes("w-full gap-4")
        preview_box = ui.column().classes("w-full gap-4")