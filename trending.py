"""Trending coins from the CoinGecko public API.

Free endpoint (`/api/v3/search/trending`), no API key. CoinGecko rate-limits
anonymous traffic, so one request is made per process and cached: `alert_bot.py`
sends several cards per run and must not fetch five times.

Design rules, matching the rest of the repository:
- never raise: a failed or rate-limited fetch degrades to an explicit
  "unavailable" line, so a missing section is visible instead of silent
- no new dependency: uses `requests`, already required
- the block is display-only; nothing here feeds decisions or history

Environment variables:
- `TRENDING_ENABLED`      default `1`; set `0` to omit the block entirely
- `TRENDING_LIMIT`        default `5`; how many coins to list
- `TRENDING_CACHE_TTL`    default `300` seconds, per process

Manual check:
    python trending.py
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

ENDPOINT = "https://api.coingecko.com/api/v3/search/trending"
SOURCE_LABEL = "CoinGecko"
DEFAULT_LIMIT = 5
DEFAULT_CACHE_TTL = 300
REQUEST_TIMEOUT = 12
USER_AGENT = "btc-alert/1.0 (+https://github.com/mehazer/btc-alert)"

_CACHE: Dict[str, Any] = {"fetched_at": 0.0, "data": None}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return os.environ.get("TRENDING_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _price_text(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if price >= 1000:
        return f"${price:,.0f}"
    if price >= 1:
        return f"${price:,.2f}"
    if price >= 0.01:
        return f"${price:.4f}"
    return f"${price:.8f}".rstrip("0").rstrip(".")


def _change_text(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    try:
        change = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{change:+.1f}%"


def parse_payload(payload: Dict[str, Any], limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
    """Normalise the CoinGecko payload into a small, stable list.

    The API nests each entry under ``coins[].item`` and stores the 24h change as
    a per-currency dictionary; both shapes are handled here so callers never
    depend on them.
    """
    items: List[Dict[str, Any]] = []
    for position, entry in enumerate(payload.get("coins") or [], start=1):
        item = (entry or {}).get("item") or {}
        data = item.get("data") or {}
        change = data.get("price_change_percentage_24h")
        if isinstance(change, dict):
            change = change.get("usd")
        items.append({
            "rank": position,
            "market_cap_rank": item.get("market_cap_rank"),
            "name": item.get("name") or "Unknown",
            "symbol": (item.get("symbol") or "").upper() or "?",
            "price_usd": data.get("price"),
            "change_24h_pct": change,
        })
        if len(items) >= max(int(limit), 1):
            break
    return items


def fetch_trending(limit: Optional[int] = None, force: bool = False) -> Dict[str, Any]:
    """Fetch trending coins, cached per process. Never raises."""
    if not enabled():
        return {"status": "disabled", "source": SOURCE_LABEL, "items": [], "reason": "TRENDING_ENABLED=0"}
    limit = _env_int("TRENDING_LIMIT", DEFAULT_LIMIT) if limit is None else int(limit)
    ttl = _env_int("TRENDING_CACHE_TTL", DEFAULT_CACHE_TTL)
    now = datetime.now(timezone.utc).timestamp()
    cached = _CACHE.get("data")
    if not force and cached is not None and (now - float(_CACHE.get("fetched_at") or 0)) < ttl:
        return cached
    try:
        response = requests.get(
            ENDPOINT,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        if response.status_code == 429:
            result = {"status": "unavailable", "source": SOURCE_LABEL, "items": [],
                      "reason": "rate limited (HTTP 429)"}
        elif response.status_code >= 400:
            result = {"status": "unavailable", "source": SOURCE_LABEL, "items": [],
                      "reason": f"HTTP {response.status_code}"}
        else:
            items = parse_payload(response.json(), limit)
            result = {
                "status": "ok" if items else "unavailable",
                "source": SOURCE_LABEL,
                "items": items,
                "reason": None if items else "empty payload",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as exc:  # network, JSON, unexpected shape
        result = {"status": "unavailable", "source": SOURCE_LABEL, "items": [],
                  "reason": f"{type(exc).__name__}: {exc}"}
    _CACHE["data"] = result
    _CACHE["fetched_at"] = now
    return result


def format_lines(data: Optional[Dict[str, Any]], limit: Optional[int] = None) -> List[str]:
    """Render the block for a Telegram card. Empty list means 'omit'."""
    if not data:
        return []
    if data.get("status") == "disabled":
        return []
    limit = _env_int("TRENDING_LIMIT", DEFAULT_LIMIT) if limit is None else int(limit)
    if data.get("status") != "ok":
        reason = data.get("reason") or "no data"
        return [f"Trending ({data.get('source') or SOURCE_LABEL}): unavailable — {reason}"]
    lines = [f"Trending ({data.get('source') or SOURCE_LABEL}, 24h):"]
    for item in (data.get("items") or [])[:max(limit, 1)]:
        rank_note = ""
        cap_rank = item.get("market_cap_rank")
        if isinstance(cap_rank, int) and cap_rank > 0:
            rank_note = f" · cap #{cap_rank}"
        lines.append(
            f"{item.get('rank')}. {item.get('symbol')} {item.get('name')}"
            f" · {_price_text(item.get('price_usd'))}"
            f" · {_change_text(item.get('change_24h_pct'))}{rank_note}"
        )
    return lines


def format_section(data: Optional[Dict[str, Any]], limit: Optional[int] = None) -> str:
    """The block as a string ready to append to a message body."""
    lines = format_lines(data, limit)
    return "\n".join(lines)


def main() -> int:
    data = fetch_trending(force=True)
    print(f"status: {data.get('status')}  source: {data.get('source')}  reason: {data.get('reason')}")
    print()
    print(format_section(data) or "(no block)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
