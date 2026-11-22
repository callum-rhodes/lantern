"""Per-browser state: the active user and the display currency.

Both are kept in ``app.storage.user`` (NiceGUI's per-browser storage), so each
browser remembers its own choice. This requires ``storage_secret`` to be set
in ``ui.run()``, and these helpers must be called while a page is being built
or from one of its event handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nicegui import app

import db
import rates

USER_KEY = "user_id"
CURRENCY_KEY = "display_currency"


# --------------------------------------------------------------------------
# Active user
# --------------------------------------------------------------------------

def get_current_user() -> dict[str, Any] | None:
    """Return the active user, or None if none exists or this browser hasn't chosen yet.

    With exactly one user, that user is selected automatically. With several,
    the browser must choose (see components._build_user_picker).
    """
    users = db.list_users()
    if not users:
        return None
    stored = app.storage.user.get(USER_KEY)
    for user in users:
        if user["id"] == stored:
            return user
    if len(users) == 1:
        app.storage.user[USER_KEY] = users[0]["id"]
        return users[0]
    return None


def set_current_user(user_id: int) -> None:
    """Remember ``user_id`` as this browser's active user."""
    app.storage.user[USER_KEY] = user_id


# --------------------------------------------------------------------------
# Display currency
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Display:
    """The currency amounts are shown in, with its rate from the base currency."""

    code: str
    rate: float = 1.0

    def money(self, cents: int | float, show_plus: bool = False) -> str:
        """Format base-currency minor units as text in the display currency."""
        return rates.format_money(cents, self.code, self.rate, show_plus)

    def major(self, cents: int | float) -> float:
        """Convert base-currency minor units to display-currency major units (charts)."""
        return rates.to_major(cents, self.rate)


def get_display(status: rates.RateStatus) -> Display:
    """The active display currency for this browser (base currency if unavailable)."""
    code = rates.effective_currency(status, app.storage.user.get(CURRENCY_KEY))
    return Display(code=code, rate=rates.rate_for(status, code))


def set_display_currency(code: str) -> None:
    """Remember ``code`` as this browser's display currency."""
    app.storage.user[CURRENCY_KEY] = code.upper()
