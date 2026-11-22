"""Shared UI pieces: the page frame (header, navigation, pickers) and first-run setup.

Every page starts with ``ctx = await build_layout("Page name")`` and builds its
content inside ``with content():``. If ``build_layout`` returns ``None`` the
first-run setup card is showing and the page should simply return.

Switching user or currency stores the choice and reloads the page, so every
table and chart is rebuilt with the new selection. This keeps the code simple.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from nicegui import app, ui

import db
import rates
import state

APP_TITLE = "Lantern"
NAV_LINKS: list[tuple[str, str]] = [
    ("Overview", "/"),
    ("Add Transactions", "/transactions"),
    ("Edit Categories", "/categories"),
    ("Export CSV", "/export"),
    ("Import CSV", "/import"),
    ("Backups", "/backups"),
]


@dataclass(frozen=True)
class PageContext:
    """What every page needs: the active user and how to show money."""

    user: dict[str, Any]
    display: state.Display
    status: rates.RateStatus

    @property
    def base_currency(self) -> str:
        """The currency amounts are stored and entered in."""
        return self.status.base


# --------------------------------------------------------------------------
# Small helpers used by the pages
# --------------------------------------------------------------------------

def notify_error(exc: Exception) -> None:
    """Show an error from the database layer as a friendly notification."""
    ui.notify(str(exc), type="negative")


def new_dialog() -> ui.dialog:
    """A dialog that removes itself from the page once it has been closed."""
    dialog = ui.dialog()
    dialog.on("hide", dialog.delete)
    return dialog


def confirm_dialog(
    title: str, message: str, confirm_text: str, on_confirm: Callable[[], None]
) -> None:
    """Pop up a confirmation dialog; ``on_confirm`` does the database work."""
    dialog = new_dialog()
    with dialog, ui.card().classes("w-96"):
        ui.label(title).classes("text-h6")
        ui.label(message)

        def confirm() -> None:
            try:
                on_confirm()
            except db.DataError as exc:
                notify_error(exc)
                return
            dialog.close()

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button(confirm_text, on_click=confirm).props("color=negative")
    dialog.open()


def amount_class(cents: int | float) -> str:
    """Text colour class: green for income (positive), red for spending (negative)."""
    return "text-positive" if cents >= 0 else "text-negative"


def info_message(text: str, icon: str = "info") -> None:
    """A muted message with an icon, e.g. for 'no data yet' states."""
    with ui.row().classes("items-center gap-2 text-grey-7"):
        ui.icon(icon)
        ui.label(text)


@contextmanager
def content() -> Iterator[ui.column]:
    """The centred content column that holds a page's body."""
    with ui.column().classes("w-full max-w-7xl mx-auto p-4 gap-4") as column:
        yield column


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

def _choose_user(user_id: int) -> None:
    """Remember the chosen user for this browser and rebuild the page."""
    state.set_current_user(user_id)
    ui.navigate.reload()


def _build_user_picker(users: list[dict[str, Any]]) -> None:
    """Full-screen card asking which user this device should view."""
    with ui.card().classes("absolute-center w-96 gap-3"):
        ui.label(f"Welcome to {APP_TITLE}").classes("text-h5")
        ui.label("Who is using the tracker on this device?").classes("text-grey-7")
        for user in users:
            ui.button(user["name"], on_click=lambda _, uid=user["id"]: _choose_user(uid)) \
                .classes("w-full")

def _switch_user(event: Any) -> None:
    """Remember the chosen user and rebuild the page."""
    state.set_current_user(event.value)
    ui.navigate.reload()


def _switch_currency(event: Any) -> None:
    """Remember the chosen display currency and rebuild the page."""
    state.set_display_currency(event.value)
    ui.navigate.reload()


def _toggle_dark(_: Any = None) -> None:
    """Flip dark mode, remember it, and rebuild the page (so charts match the theme)."""
    app.storage.user["dark_mode"] = not app.storage.user.get("dark_mode", False)
    ui.navigate.reload()


def _build_add_user_dialog() -> ui.dialog:
    """A dialog for creating a new user (hidden until opened)."""
    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label("Add a user").classes("text-h6")
        name = ui.input("Name").props("autofocus")

        def create() -> None:
            try:
                user_id = db.add_user(name.value)
            except db.DataError as exc:
                notify_error(exc)
                return
            state.set_current_user(user_id)
            ui.navigate.reload()

        name.on("keydown.enter", create)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Create", on_click=create)
    return dialog


def _build_delete_user_dialog(current_user_id: int) -> ui.dialog:
    """A warning dialog for permanently deleting a user and all their data."""
    users = db.list_users()
    with ui.dialog() as dialog, ui.card().classes("w-96 gap-3"):
        ui.label("Delete a user").classes("text-h6")
        choice = ui.select(
            {u["id"]: u["name"] for u in users}, value=current_user_id, label="User to delete",
        ).classes("w-full")
        warning = ui.label().classes("text-negative")
        last_user_note = ui.label(
            "This is the only user, so you will be asked to create a new one afterwards."
        ).classes("text-grey-7")
        last_user_note.set_visibility(len(users) == 1)

        def update_warning() -> None:
            name = next(u["name"] for u in users if u["id"] == choice.value)
            count = db.transaction_count(choice.value)
            warning.text = (
                f"This permanently deletes '{name}' and everything they have recorded: "
                f"{count} transaction(s) and all of their categories. "
                "This cannot be undone. Make a backup first if you are unsure."
            )

        choice.on_value_change(update_warning)
        update_warning()

        def delete() -> None:
            uid = choice.value
            try:
                db.delete_user(uid)
            except db.DataError as exc:
                notify_error(exc)
                return
            remaining = db.list_users()
            if uid == current_user_id and remaining:  # move this browser to someone else
                state.set_current_user(remaining[0]["id"])
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete permanently", on_click=delete).props("color=negative")
    return dialog


def _build_header(ctx: PageContext, active: str) -> None:
    """Title, navigation, user picker, currency selector and dark-mode toggle."""
    ui.dark_mode(value=bool(app.storage.user.get("dark_mode")))
    add_user_dialog = _build_add_user_dialog()
    delete_user_dialog = _build_delete_user_dialog(ctx.user["id"])
    select_props = "dense options-dense outlined dark"

    with ui.header().classes("items-center justify-between gap-4"):
        with ui.row().classes("items-center gap-6"):
            ui.label(APP_TITLE).classes("text-h6")
            for label, path in NAV_LINKS:
                link = ui.link(label, path).classes("text-white no-underline")
                if label == active:
                    link.classes("font-bold underline")

        with ui.row().classes("items-center gap-2"):
            users = {u["id"]: u["name"] for u in db.list_users()}
            ui.select(users, value=ctx.user["id"], on_change=_switch_user) \
                .props(select_props).classes("min-w-32").tooltip("Active user")
            ui.button(icon="person_add", on_click=add_user_dialog.open) \
                .props("flat round dense color=white").tooltip("Add user")
            ui.button(icon="person_remove", on_click=delete_user_dialog.open) \
                .props("flat round dense color=white").tooltip("Delete user")
            ui.select(ctx.status.currencies, value=ctx.display.code, on_change=_switch_currency) \
                .props(select_props).classes("w-24").tooltip("Display currency")
            ui.button(icon="dark_mode", on_click=_toggle_dark) \
                .props("flat round dense color=white").tooltip("Toggle dark mode")


# --------------------------------------------------------------------------
# First-run setup
# --------------------------------------------------------------------------

def _build_first_run_setup(need_currency: bool, need_user: bool) -> None:
    """Welcome card that sets the base currency and/or creates the first user."""
    with ui.card().classes("absolute-center w-96 gap-3"):
        ui.label(f"Welcome to {APP_TITLE}").classes("text-h5")
        currency = None
        if need_currency:
            ui.label(
                "Which currency do you earn and record everything in? "
                "All amounts are stored in it, so choose carefully."
            ).classes("text-grey-7")
            currency = ui.select(
                list(rates.CURRENCY_SYMBOLS), value=db.DEFAULT_BASE_CURRENCY,
                label="Base currency", with_input=True, new_value_mode="add-unique",
            ).classes("w-full")
        name = None
        if need_user:
            ui.label("Create the first user. Each user has their own transactions and categories.") \
                .classes("text-grey-7")
            name = ui.input("Name").classes("w-full").props("autofocus")

        def finish() -> None:
            if name is not None and not (name.value or "").strip():
                ui.notify("Please enter a name.", type="warning")
                return
            try:
                if currency is not None:
                    db.set_base_currency(currency.value)
                if name is not None:
                    state.set_current_user(db.add_user(name.value))
            except db.DataError as exc:
                notify_error(exc)
                return
            ui.navigate.reload()

        if name is not None:
            name.on("keydown.enter", finish)
        ui.button("Get started", on_click=finish).classes("w-full")


# --------------------------------------------------------------------------
# Page frame
# --------------------------------------------------------------------------

async def build_layout(active: str) -> PageContext | None:
    """Build the shared header and return the page context.

    ``active`` is the navigation label to highlight. Returns ``None`` (after
    showing the setup card) when the base currency or the first user is missing.
    """
    users = db.list_users()
    user = state.get_current_user()
    need_currency = not db.is_base_currency_set()
    if need_currency or not users:
        _build_first_run_setup(need_currency=need_currency, need_user=not users)
        return None
    if user is None:
        _build_user_picker(users)
        return None

    status = await rates.ensure_rates_async()
    ctx = PageContext(user=user, display=state.get_display(status), status=status)
    _build_header(ctx, active)
    if status.warning:
        ui.label(status.warning).classes(
            "text-caption bg-orange-2 text-orange-10 w-full text-center q-pa-xs"
        )
    return ctx
