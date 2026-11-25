"""Exchange-rate cache table (fetching and conversion live in rates.py)."""
from __future__ import annotations

from datetime import datetime, timezone

from db.core import connect


def save_rates(base: str, rates: dict[str, float], fetched_at: str | None = None) -> str:
    """Replace the cached rates for ``base``; return the fetch timestamp (UTC ISO)."""
    stamp = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("DELETE FROM exchange_rates WHERE base = ?", (base,))
        conn.executemany(
            "INSERT INTO exchange_rates(base, target, rate, fetched_at) VALUES (?, ?, ?, ?)",
            [(base, code, float(rate), stamp) for code, rate in rates.items()],
        )
    return stamp


def load_rates(base: str) -> tuple[dict[str, float], str | None]:
    """Return ``(rates, fetched_at)`` from the cache; ``({}, None)`` if empty."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT target, rate, fetched_at FROM exchange_rates WHERE base = ?", (base,)
        ).fetchall()
    if not rows:
        return {}, None
    return {r["target"]: r["rate"] for r in rows}, max(r["fetched_at"] for r in rows)
