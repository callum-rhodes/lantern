"""Shared UI pieces: the page frame (header, navigation, pickers) and first-run setup.

Every page starts with ``ctx = await build_layout("Page name")`` and builds its
content inside ``with content():``. If ``build_layout`` returns ``None`` the
first-run setup card is showing and the page should simply return.

Switching user or currency stores the choice and reloads the page, so every
table and chart is rebuilt with the new selection. This keeps the code simple.
"""
from __future__ import annotations

from pathlib import Path

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from nicegui import app, ui

from db import DataError, users, settings, transactions, rates
from web_app import state

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.add_static_files("/static", STATIC_DIR)

APP_TITLE = "Lantern"
LOGO_SVG = (STATIC_DIR / "lantern.svg").read_text(encoding="utf-8")
NAV_LINKS_MAIN: list[tuple[str, str, str]] = [
    ("Overview", "/", "bar_chart"),
    ("Add Transactions", "/transactions", "add"),
    ("Shared Balances", "/shared", "group"),
]
NAV_LINKS_OTHER: list[tuple[str, str, str]] = [
    ("Edit Categories", "/categories", ""),
    ("Export CSV", "/export", ""),
    ("Import CSV", "/import", ""),
    ("Backups", "/backups", ""),
]

PALETTE = {
    "primary": "#1f2a44",    # deep navy: header, main buttons
    "secondary": "#f2a93b",  # lantern amber: accents
    "accent": "#f2a93b",
    "positive": "#2e9e6b",   # softer green (income)
    "negative": "#d1493f",   # softer red (spending)
    "warning": "#e08a1e",
    "info": "#3b6ea5",
}

def apply_theme() -> None:
    """Brand colours plus a warm off-white page background in light mode."""
    ui.colors(**PALETTE)
    ui.add_css("""
        body.body--light { background: #f6f7fb; }
        .q-card { border-radius: 12px; box-shadow: 0 1px 3px rgba(31,42,68,.10); }
        .q-header { background: #1f2a44 !important; }
        .q-btn.bg-primary:hover { background: #2b3a5e !important; }

        /* nav buttons */
        .q-btn.nav-btn { color: #cbd2e3 !important; border-radius: 0; border-bottom: 3px solid transparent; }
        .q-btn.nav-btn:hover { color: #fff !important; }
        .q-btn.nav-active { color: #fff !important; border-bottom-color: #f2a93b; font-weight: 600; }

        /* dark mode: use amber wherever the app uses the primary colour */
        body.body--dark { --q-primary: #f2a93b !important; }
        /* dark mode: dark text on amber filled buttons and toggles */
        body.body--dark .q-btn.bg-primary,
        body.body--dark .q-btn.bg-primary .q-btn__content,
        body.body--dark .q-btn.bg-primary .q-icon { color: #1f2a44 !important; }
        .q-header { --q-primary: #1f2a44; }
        /* dark mode: dark text/ticks on amber for uploaders and checkboxes */
        body.body--dark .q-uploader__header,
        body.body--dark .q-uploader__header .q-uploader__title,
        body.body--dark .q-uploader__header .q-uploader__subtitle,
        body.body--dark .q-uploader__header .q-btn,
        body.body--dark .q-uploader__header .q-icon { color: #1f2a44 !important; }
        body.body--dark .q-checkbox__svg,
        body.body--dark .q-checkbox__truthy { color: #1f2a44 !important; stroke: #1f2a44 !important; }
    """)
    ui.add_css("""
        @font-face {
            font-family: 'Manrope';
            src: url('/static/fonts/Manrope-VariableFont_wght.ttf') format('truetype');
            font-weight: 200 800;
            font-display: swap;
        }
        body, .q-btn, .q-field, .q-table, .q-item { font-family: 'Manrope', sans-serif; }
        .q-table td, .text-h5, .text-h6, .text-subtitle1 { font-variant-numeric: tabular-nums; }
    """)

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
            except DataError as exc:
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


def _build_login(user_list: list[dict[str, Any]]) -> None:
    """Login card. Users without a password are asked to choose one instead."""
    by_id = {u["id"]: u for u in user_list}
    stored = app.storage.user.get(state.USER_KEY)
    with ui.card().classes("absolute-center w-96 gap-3"):
        ui.label(f"Welcome to {APP_TITLE}").classes("text-h5")
        choice = ui.select(
            {u["id"]: u["name"] for u in user_list},
            value=stored if stored in by_id else user_list[0]["id"], label="User",
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
                        except DataError as exc:
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
                            users.set_password(user["id"], first.value or "")
                            state.log_in(user["id"])
                        except DataError as exc:
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
            if not users.verify_password(user_id, current.value or ""):
                ui.notify("Current password is wrong.", type="negative")
                return
            try:
                users.set_password(user_id, first.value or "")
            except DataError as exc:
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
                users.add_user(name.value, first.value or "")
            except DataError as exc:
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
            f"{transactions.transaction_count(user['id'])} transaction(s) and all of their categories. "
            "This cannot be undone. Make a backup first if you are unsure."
        ).classes("text-negative")
        password = ui.input("Enter your password to confirm", password=True,
                            password_toggle_button=True).classes("w-full")

        def delete() -> None:
            if not users.verify_password(user["id"], password.value or ""):
                ui.notify("Wrong password.", type="negative")
                return
            try:
                users.delete_user(user["id"])
            except DataError as exc:
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
    app.storage.user["dark_mode"] = not app.storage.user.get("dark_mode", True)
    ui.navigate.reload()


def _build_header(ctx: PageContext, active: str) -> None:
    """Title, navigation, user picker, currency selector and dark-mode toggle."""
    add_user_dialog = _build_add_user_dialog()
    password_dialog = _build_change_password_dialog(ctx.user["id"])
    delete_user_dialog = _build_delete_user_dialog(ctx.user)
    select_props = "dense options-dense outlined dark"

    with ui.header().classes("items-center justify-between gap-4 px-6 py-1"):
        with ui.row().classes("items-center gap-6"):
            with ui.row().classes("items-center gap-2"):
                ui.html(LOGO_SVG, sanitize=False).classes("w-8 h-8")
                ui.label(APP_TITLE).classes("text-h5 text-bold")
            with ui.row().classes("items-center gap-1"):
                for label, path, icon in NAV_LINKS_MAIN:
                    ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p)) \
                        .props("flat no-caps color=white").classes("nav-btn" + (" nav-active" if label == active else ""))
                with ui.button("More", icon="expand_more").props("flat no-caps color=white").classes("nav-btn" \
                                    + (" nav-active" if any(active == label for label,_,_ in NAV_LINKS_OTHER) else "")):
                    with ui.menu():
                        for label, path, _ in NAV_LINKS_OTHER:
                            ui.menu_item(label, on_click=lambda p=path: ui.navigate.to(p))

        with ui.row().classes("items-center gap-2"):
            ui.select(ctx.status.currencies, value=ctx.display.code, on_change=_switch_currency) \
                .props(select_props).classes("w-24").tooltip("Display currency")
            ui.button(icon="dark_mode", on_click=_toggle_dark) \
                .props("flat round dense color=white").tooltip("Toggle dark mode")
            with ui.button(ctx.user["name"], icon="account_circle").props("flat no-caps").classes("text-white"):
                with ui.menu():
                    ui.menu_item("Add user", on_click=add_user_dialog.open)
                    ui.menu_item("Change password", on_click=password_dialog.open)
                    ui.menu_item("Delete my account", on_click=delete_user_dialog.open).classes("text-negative")
                    ui.separator()
                    ui.menu_item("Log out", on_click=_log_out)

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
                list(rates.CURRENCY_SYMBOLS), value=settings.DEFAULT_BASE_CURRENCY,
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
                    settings.set_base_currency(currency.value)
                if name is not None:
                    # new: create the user with their password, then log this device in
                    state.log_in(users.add_user(name.value, first.value or ""))
            except DataError as exc:
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
    apply_theme()
    ui.dark_mode(value=app.storage.user.get("dark_mode", True))
    user_list = users.list_users()
    need_currency = not settings.is_base_currency_set()
    if need_currency or not user_list:
        _build_first_run_setup(need_currency=need_currency, need_user=not user_list)
        return None
    user = state.get_current_user()
    if user is None:
        _build_login(user_list)
        return None

    status = await rates.ensure_rates_async()
    ctx = PageContext(user=user, display=state.get_display(status), status=status)
    _build_header(ctx, active)
    if status.warning:
        ui.label(status.warning).classes(
            "text-caption bg-orange-2 text-orange-10 w-full text-center q-pa-xs"
        )
    return ctx
