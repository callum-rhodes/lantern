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

def _password_fields(label: str = "Password") -> tuple[ui.input, ui.input]:
    first = ui.input(label, password=True, password_toggle_button=True).classes("w-full")
    second = ui.input("Repeat password", password=True, password_toggle_button=True).classes("w-full")
    return first, second


def _passwords_match(first: ui.input, second: ui.input) -> bool:
    if first.value != second.value:
        ui.notify("The two passwords don't match.", type="warning")
        return False
    return True


def _build_login(users: list[dict[str, Any]]) -> None:
    """Login card. Users without a password are asked to choose one instead."""
    by_id = {u["id"]: u for u in users}
    stored = app.storage.user.get(state.USER_KEY)
    with ui.card().classes("absolute-center w-96 gap-3"):
        ui.label(f"Welcome to {APP_TITLE}").classes("text-h5")
        choice = ui.select(
            {u["id"]: u["name"] for u in users},
            value=stored if stored in by_id else users[0]["id"], label="User",
        ).classes("w-full")
        form = ui.column().classes("w-full gap-3")

        def show_form() -> None:
            form.clear()
            user = by_id[choice.value]
            with form:
                if user["has_password"]:
                    password = ui.input("Password", password=True, password_toggle_button=True) \
                        .classes("w-full").props("autofocus")

                    def submit() -> None:
                        try:
                            state.try_login(user["id"], password.value or "")
                        except db.DataError as exc:
                            notify_error(exc)
                            password.value = ""
                            return
                        ui.navigate.reload()

                    password.on("keydown.enter", submit)
                    ui.button("Log in", on_click=submit).classes("w-full")
                else:
                    ui.label("This user has no password yet. Choose one to continue.") \
                        .classes("text-grey-7")
                    first, second = _password_fields("New password")

                    def submit() -> None:
                        if not _passwords_match(first, second):
                            return
                        try:
                            db.set_password(user["id"], first.value or "")
                            state.log_in(user["id"])
                        except db.DataError as exc:
                            notify_error(exc)
                            return
                        ui.navigate.reload()

                    second.on("keydown.enter", submit)
                    ui.button("Set password and log in", on_click=submit).classes("w-full")

        choice.on_value_change(show_form)
        show_form()


def _log_out(_: Any = None) -> None:
    state.log_out()
    ui.navigate.reload()


def _build_change_password_dialog(user_id: int) -> ui.dialog:
    with ui.dialog() as dialog, ui.card().classes("w-80 gap-3"):
        ui.label("Change password").classes("text-h6")
        current = ui.input("Current password", password=True, password_toggle_button=True) \
            .classes("w-full")
        first, second = _password_fields("New password")

        def save() -> None:
            if not _passwords_match(first, second):
                return
            if not db.verify_password(user_id, current.value or ""):
                ui.notify("Current password is wrong.", type="negative")
                return
            try:
                db.set_password(user_id, first.value or "")
            except db.DataError as exc:
                notify_error(exc)
                return
            state.log_in(user_id)  # this device stays logged in; others are logged out
            dialog.close()
            ui.notify("Password changed. Other devices will need to log in again.", type="positive")

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Change", on_click=save)
    return dialog


def _build_add_user_dialog() -> ui.dialog:
    with ui.dialog() as dialog, ui.card().classes("w-80 gap-3"):
        ui.label("Add a user").classes("text-h6")
        name = ui.input("Name").props("autofocus").classes("w-full")
        first, second = _password_fields("Password")

        def create() -> None:
            if not _passwords_match(first, second):
                return
            try:
                db.add_user(name.value, first.value or "")
            except db.DataError as exc:
                notify_error(exc)
                return
            dialog.close()
            ui.notify(f"Created '{name.value.strip()}'. They can now log in.", type="positive")

        second.on("keydown.enter", create)
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Create", on_click=create)
    return dialog


def _build_delete_user_dialog(user: dict[str, Any]) -> ui.dialog:
    """Users can only delete their own account, after confirming their password."""
    with ui.dialog() as dialog, ui.card().classes("w-96 gap-3"):
        ui.label("Delete your account").classes("text-h6")
        ui.label(
            f"This permanently deletes '{user['name']}' and everything they have recorded: "
            f"{db.transaction_count(user['id'])} transaction(s) and all of their categories. "
            "This cannot be undone. Make a backup first if you are unsure."
        ).classes("text-negative")
        password = ui.input("Enter your password to confirm", password=True,
                            password_toggle_button=True).classes("w-full")

        def delete() -> None:
            if not db.verify_password(user["id"], password.value or ""):
                ui.notify("Wrong password.", type="negative")
                return
            try:
                db.delete_user(user["id"])
            except db.DataError as exc:
                notify_error(exc)
                return
            state.log_out()
            ui.navigate.reload()

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete permanently", on_click=delete).props("color=negative")
    return dialog


def _switch_currency(event: Any) -> None:
    """Remember the chosen display currency and rebuild the page."""
    state.set_display_currency(event.value)
    ui.navigate.reload()


def _toggle_dark(_: Any = None) -> None:
    """Flip dark mode, remember it, and rebuild the page (so charts match the theme)."""
    app.storage.user["dark_mode"] = not app.storage.user.get("dark_mode", False)
    ui.navigate.reload()


def _build_header(ctx: PageContext, active: str) -> None:
    """Title, navigation, user picker, currency selector and dark-mode toggle."""
    ui.dark_mode(value=bool(app.storage.user.get("dark_mode")))
    add_user_dialog = _build_add_user_dialog()
    password_dialog = _build_change_password_dialog(ctx.user["id"])
    delete_user_dialog = _build_delete_user_dialog(ctx.user)
    select_props = "dense options-dense outlined dark"

    with ui.header().classes("items-center justify-between gap-4"):
        with ui.row().classes("items-center gap-6"):
            ui.label(APP_TITLE).classes("text-h6")
            for label, path in NAV_LINKS:
                link = ui.link(label, path).classes("text-white no-underline")
                if label == active:
                    link.classes("font-bold underline")

        with ui.row().classes("items-center gap-2"):
            ui.label(ctx.user["name"]).classes("text-body1")
            ui.button(icon="person_add", on_click=add_user_dialog.open) \
                .props("flat round dense color=white").tooltip("Add user")
            ui.button(icon="key", on_click=password_dialog.open) \
                .props("flat round dense color=white").tooltip("Change password")
            ui.button(icon="person_remove", on_click=delete_user_dialog.open) \
                .props("flat round dense color=white").tooltip("Delete my account")
            ui.button(icon="logout", on_click=_log_out) \
                .props("flat round dense color=white").tooltip("Log out")
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
        name = first = second = None
        if need_user:
            ui.label("Create the first user. Each user has their own transactions and categories.") \
                .classes("text-grey-7")
            name = ui.input("Name").classes("w-full").props("autofocus")
            first, second = _password_fields("Password")  # new: password + repeat boxes

        def finish() -> None:
            if name is not None:
                if not (name.value or "").strip():
                    ui.notify("Please enter a name.", type="warning")
                    return
                if not _passwords_match(first, second):   # new: stop if the two boxes differ
                    return
            try:
                if currency is not None:
                    db.set_base_currency(currency.value)
                if name is not None:
                    # new: create the user with their password, then log this device in
                    state.log_in(db.add_user(name.value, first.value or ""))
            except db.DataError as exc:
                notify_error(exc)
                return
            ui.navigate.reload()

        if second is not None:
            second.on("keydown.enter", finish)            # Enter in the last box submits
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
    need_currency = not db.is_base_currency_set()
    if need_currency or not users:
        _build_first_run_setup(need_currency=need_currency, need_user=not users)
        return None
    user = state.get_current_user()
    if user is None:
        _build_login(users)
        return None

    status = await rates.ensure_rates_async()
    ctx = PageContext(user=user, display=state.get_display(status), status=status)
    _build_header(ctx, active)
    if status.warning:
        ui.label(status.warning).classes(
            "text-caption bg-orange-2 text-orange-10 w-full text-center q-pa-xs"
        )
    return ctx
