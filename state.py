"""Per-browser state: the active user and the display currency.

Both are kept in ``app.storage.user`` (NiceGUI's per-browser storage), so each
browser remembers its own choice. This requires ``storage_secret`` to be set
in ``ui.run()``, and these helpers must be called while a page is being built
or from one of its event handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import hmac, time

from nicegui import app

from db import DataError, users
import rates

# --------------------------------------------------------------------------
# Active user
# --------------------------------------------------------------------------

USER_KEY = "user_id"
CURRENCY_KEY = "display_currency"
AUTH_KEY = "auth"
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
_failures: dict[int, list[float]] = {}


def get_current_user() -> dict[str, Any] | None:
    """The logged-in user, or None if this browser isn't logged in (or the login is stale)."""
    stored = app.storage.user.get(USER_KEY)
    if stored is None:
        return None
    user = users.get_user(stored)
    token = users.session_token(stored) if user else None
    if token is None or not hmac.compare_digest(str(app.storage.user.get(AUTH_KEY, "")), token):
        return None
    return user


def log_in(user_id: int) -> None:
    """Remember this device as logged in (valid until the password changes or they log out)."""
    app.storage.user[USER_KEY] = user_id
    app.storage.user[AUTH_KEY] = users.session_token(user_id)


def log_out() -> None:
    app.storage.user.pop(AUTH_KEY, None)  # keep USER_KEY so the login card preselects them


def try_login(user_id: int, password: str) -> None:
    """Log in or raise ``db.DataError``. Locks the user briefly after repeated failures."""
    now = time.monotonic()
    recent = [t for t in _failures.get(user_id, []) if now - t < LOCKOUT_SECONDS]
    if len(recent) >= MAX_FAILURES:
        wait = int(LOCKOUT_SECONDS - (now - recent[0])) + 1
        raise DataError(f"Too many wrong passwords. Try again in {wait} seconds.")
    if not users.verify_password(user_id, password):
        _failures[user_id] = [*recent, now]
        raise DataError("Wrong password.")
    _failures.pop(user_id, None)
    log_in(user_id)


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
