"""Point-in-time rules for dated market, news and sentiment inputs.

Adapted from TradingAgents dataflows/date_window.py ideas:
- Clamp requested windows so live-only feeds cannot pretend to cover history.
- Trim dated items to an analysis window.
- Report coverage gaps instead of treating empty results as "no news".
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any


def to_utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC-aware; naive values are treated as UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def get_current_date() -> str:
    return date.today().strftime("%Y-%m-%d")


def is_historical(run_date: str | None) -> bool:
    return bool(run_date) and str(run_date) < get_current_date()


def parse_timestamp(raw: Any) -> datetime | None:
    """Parse ISO, RFC 2822, or unix timestamps into UTC datetimes."""
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, (int, float)):
            value = float(raw)
            if value > 1e12:
                value /= 1000.0
            return datetime.fromtimestamp(value, tz=UTC)
        text = str(raw).strip()
        try:
            dt = parsedate_to_datetime(text)
            return to_utc(dt)
        except Exception:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return to_utc(dt)
    except Exception:
        return None


def in_window(pub_dt: datetime | None, start_dt: datetime, end_dt: datetime) -> bool:
    """Whether an item belongs in the half-open window [start, end + 1 day).

    Undated items are kept only when the window reaches the present.
    """
    end = to_utc(end_dt)
    if pub_dt is not None:
        return to_utc(start_dt) <= to_utc(pub_dt) < end + timedelta(days=1)
    return end >= datetime.now(UTC) - timedelta(days=1)


def filter_items_to_window(
    items: list[dict[str, Any]],
    *,
    start_dt: datetime,
    end_dt: datetime,
    timestamp_keys: tuple[str, ...] = ("published", "time", "date", "created_at"),
) -> tuple[list[dict[str, Any]], list[datetime]]:
    """Keep items inside the analysis window and return observed timestamps."""
    kept: list[dict[str, Any]] = []
    observed: list[datetime] = []
    for item in items:
        raw = None
        for key in timestamp_keys:
            if item.get(key) is not None:
                raw = item.get(key)
                break
        pub_dt = parse_timestamp(raw)
        if not in_window(pub_dt, start_dt, end_dt):
            continue
        kept.append(item)
        if pub_dt is not None:
            observed.append(pub_dt)
    return kept, observed


def coverage_gap(
    observed_dates: list[datetime],
    start_date: str,
    end_date: str,
    source: str,
    subject: str,
) -> str | None:
    """Explain when a feed cannot cover the requested window.

    Returns None when an empty result can honestly mean "no items".
    """
    now = datetime.now(UTC)
    oldest = min((to_utc(d) for d in observed_dates if d is not None), default=now)
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return (
            f"<{source} unavailable for {start_date}..{end_date}: "
            f"invalid window, so this is not an absence of {subject}>"
        )
    if end > now.date():
        reason = "the window extends past today"
    elif oldest.date() > start:
        reason = "it only serves recent items"
    else:
        return None
    return (
        f"<{source} unavailable for {start_date}..{end_date}: "
        f"{reason}, so this is not an absence of {subject}>"
    )


def recent_window_hours(hours: int = 24) -> tuple[datetime, datetime, str, str]:
    """Return (start_dt, end_dt, start_date, end_date) for a live lookback window."""
    end_dt = datetime.now(UTC)
    start_dt = end_dt - timedelta(hours=hours)
    return start_dt, end_dt, start_dt.date().isoformat(), end_dt.date().isoformat()
