"""Key/value settings and the base currency."""
from __future__ import annotations

from db.core import DataError, connect

DEFAULT_BASE_CURRENCY = "GBP"


def get_setting(key: str, default: str | None = None) -> str | None:
    """Read one setting value."""
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Create or update one setting."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def is_base_currency_set() -> bool:
    """True once the base currency has been chosen (false on first run)."""
    return get_setting("base_currency") is not None


def get_base_currency() -> str:
    """Return the base currency code (default GBP)."""
    return get_setting("base_currency", DEFAULT_BASE_CURRENCY) or DEFAULT_BASE_CURRENCY


def set_base_currency(code: str) -> None:
    """Store the base currency; all recorded amounts are in this currency."""
    code = (code or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise DataError("Currency codes are three letters, e.g. GBP.")
    set_setting("base_currency", code)
