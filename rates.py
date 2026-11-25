"""Exchange rates and money helpers.

* Rates come from the free Frankfurter API and are cached in SQLite
  (``db.save_rates`` / ``db.load_rates``); the cache is refreshed when older
  than 24 hours.
* If the network fails, cached rates are used and a warning is returned.
  With no cache at all, only the base currency is available.
* Stored amounts are always in the base currency. Conversion happens only
  when displaying, via ``format_money`` / ``to_major``.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import httpx

from db import DataError, rate_cache, settings

log = logging.getLogger(__name__)

API_URL = "https://api.frankfurter.app/latest"
CACHE_MAX_AGE = timedelta(hours=24)
RETRY_AFTER_FAILURE_SECONDS = 600  # don't hammer the API (or wait on timeouts) while offline
REQUEST_TIMEOUT_SECONDS = 10

CURRENCY_SYMBOLS: dict[str, str] = {
    "GBP": "£", "USD": "$", "EUR": "€", "JPY": "¥", "CNY": "CN¥", "INR": "₹",
    "KRW": "₩", "CHF": "CHF", "AUD": "A$", "CAD": "C$", "NZD": "NZ$",
    "HKD": "HK$", "SGD": "S$", "MXN": "MX$", "BRL": "R$", "ZAR": "R",
    "SEK": "kr", "NOK": "kr", "DKK": "kr", "PLN": "zł", "CZK": "Kč",
    "TRY": "₺", "ILS": "₪", "THB": "฿", "PHP": "₱",
}

_lock = threading.Lock()
_last_failed_attempt: float | None = None  # time.monotonic() of the last failed fetch


@dataclass(frozen=True)
class RateStatus:
    """Rates available for display, plus freshness information."""

    base: str
    rates: dict[str, float] = field(default_factory=dict)  # always includes base -> 1.0
    fetched_at: str | None = None
    warning: str | None = None

    @property
    def currencies(self) -> list[str]:
        """Currency codes that can be selected, base currency first."""
        others = sorted(code for code in self.rates if code != self.base)
        return [self.base, *others]


# --------------------------------------------------------------------------
# Fetching and caching
# --------------------------------------------------------------------------

def _fetch_remote(base: str) -> dict[str, float]:
    """Download the latest rates for ``base`` from Frankfurter."""
    response = httpx.get(
        API_URL,
        params={"from": base},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    response.raise_for_status()
    rates = response.json()["rates"]
    cleaned = {str(code).upper(): float(rate) for code, rate in rates.items() if float(rate) > 0}
    if not cleaned:
        raise ValueError("The API returned no rates.")
    return cleaned


def _is_stale(fetched_at: str | None) -> bool:
    """True if the cache is missing, unreadable or older than 24 hours."""
    if not fetched_at:
        return True
    try:
        stamp = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - stamp > CACHE_MAX_AGE


def format_fetched_at(fetched_at: str | None) -> str:
    """Human-readable 'last updated' time, e.g. '21 Nov 2026 16:19 UTC'."""
    if not fetched_at:
        return "never"
    try:
        stamp = datetime.fromisoformat(fetched_at).astimezone(timezone.utc)
    except ValueError:
        return fetched_at
    return stamp.strftime("%d %b %Y %H:%M UTC")


def ensure_rates(base: str | None = None, force: bool = False) -> RateStatus:
    """Return usable rates, refreshing the cache first when it is older than 24 hours.

    Blocking (it may make a network call); use ``ensure_rates_async`` from
    NiceGUI handlers. Never raises for network or cache problems; those are
    reported through ``RateStatus.warning``.
    """
    global _last_failed_attempt
    base = (base or settings.get_base_currency()).upper()
    with _lock:
        cached, fetched_at = rate_cache.load_rates(base)
        warning: str | None = None

        recently_failed = (
            _last_failed_attempt is not None
            and time.monotonic() - _last_failed_attempt < RETRY_AFTER_FAILURE_SECONDS
        )
        if force or (_is_stale(fetched_at) and not recently_failed):
            try:
                fresh = _fetch_remote(base)
                try:
                    fetched_at = rate_cache.save_rates(base, fresh)
                except DataError:
                    log.warning("Could not cache exchange rates", exc_info=True)
                    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                cached = fresh
                _last_failed_attempt = None
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                log.warning("Could not fetch exchange rates: %s", exc)
                _last_failed_attempt = time.monotonic()

        if _is_stale(fetched_at):
            if cached:
                warning = (
                    "Could not update exchange rates (offline?). "
                    f"Showing rates last updated {format_fetched_at(fetched_at)}."
                )
            else:
                warning = f"Exchange rates are unavailable, so only {base} can be shown."

    rates = {**cached, base: 1.0}
    return RateStatus(base=base, rates=rates, fetched_at=fetched_at, warning=warning)


async def ensure_rates_async(base: str | None = None, force: bool = False) -> RateStatus:
    """Async wrapper so the network call does not block the NiceGUI event loop."""
    return await asyncio.to_thread(ensure_rates, base, force)


# --------------------------------------------------------------------------
# Conversion and formatting
# --------------------------------------------------------------------------

def effective_currency(status: RateStatus, wanted: str | None) -> str:
    """The currency to really use: ``wanted`` if a rate exists, else the base currency."""
    if wanted and wanted.upper() in status.rates:
        return wanted.upper()
    return status.base


def rate_for(status: RateStatus, currency: str) -> float:
    """Multiplier from the base currency to ``currency`` (1.0 for the base itself)."""
    return status.rates[currency.upper()]


def amount_to_cents(value: str | int | float | Decimal) -> int:
    """Parse a user-entered amount in major units (e.g. '12.50') into minor units."""
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise DataError("Please enter the amount as a number, e.g. 12.50.") from exc
    if not amount.is_finite():
        raise DataError("Please enter the amount as a number, e.g. 12.50.")
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_major(cents: int | float, rate: float = 1.0) -> float:
    """Convert base-currency minor units to display-currency major units (for charts)."""
    value = Decimal(str(cents)) * Decimal(str(rate)) / 100
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def format_money(
    cents: int | float, currency: str, rate: float = 1.0, show_plus: bool = False
) -> str:
    """The one place money is formatted: symbol, thousands separators, 2 decimals.

    ``cents`` is in the base currency; it is converted with ``rate`` first.
    """
    value = Decimal(str(to_major(cents, rate)))
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), currency.upper())
    if symbol.isalpha():
        symbol += " "
    sign = "-" if value < 0 else ("+" if show_plus and value > 0 else "")
    return f"{sign}{symbol}{abs(value):,.2f}"
