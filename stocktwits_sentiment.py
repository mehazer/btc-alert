"""Free live StockTwits sentiment for major crypto symbols.

Public stream endpoint requires no API key. This feed is live-only: it is not
an archive, so historical coverage gaps must be reported rather than treated
as neutral sentiment.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from date_window import coverage_gap, filter_items_to_window, parse_timestamp, recent_window_hours

STOCKTWITS_STREAM = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"

# StockTwits crypto symbols commonly use .X suffix.
DEFAULT_SYMBOLS = {
    "BTC": "BTC.X",
    "ETH": "ETH.X",
    "SOL": "SOL.X",
}


def _message_to_item(message: dict[str, Any], symbol_key: str) -> dict[str, Any] | None:
    body = (message.get("body") or "").strip()
    if not body:
        return None
    created = message.get("created_at")
    user = (message.get("user") or {}).get("username") or "unknown"
    entities = message.get("entities") or {}
    sentiment = ((entities.get("sentiment") or {}).get("basic") or "").lower()
    return {
        "source": "StockTwits",
        "type": "stocktwits",
        "symbol": symbol_key,
        "headline": body[:280],
        "published": created,
        "user": user,
        "sentiment_label": sentiment if sentiment in {"bullish", "bearish"} else "none",
        "message_id": message.get("id"),
    }


def fetch_symbol_stream(symbol_key: str = "BTC", limit: int = 30) -> dict[str, Any]:
    """Fetch recent StockTwits messages and summarize bullish/bearish share."""
    st_symbol = DEFAULT_SYMBOLS.get(symbol_key.upper(), f"{symbol_key.upper()}.X")
    start_dt, end_dt, start_date, end_date = recent_window_hours(24)
    payload: dict[str, Any] = {
        "status": "ok",
        "symbol": symbol_key.upper(),
        "stocktwits_symbol": st_symbol,
        "window": {"start": start_date, "end": end_date, "hours": 24},
        "messages": [],
        "summary": {
            "message_count": 0,
            "bullish": 0,
            "bearish": 0,
            "neutral": 0,
            "bullish_ratio": None,
            "score": None,
        },
        "coverage_gap": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        response = requests.get(
            STOCKTWITS_STREAM.format(symbol=st_symbol),
            params={"limit": max(1, min(limit, 30))},
            timeout=15,
            headers={"User-Agent": "btc-alert/1.0"},
        )
        response.raise_for_status()
        data = response.json()
        raw_messages = data.get("messages") or []
        items = []
        for message in raw_messages:
            item = _message_to_item(message, symbol_key.upper())
            if item:
                items.append(item)
        kept, observed = filter_items_to_window(items, start_dt=start_dt, end_dt=end_dt)
        bullish = sum(1 for x in kept if x.get("sentiment_label") == "bullish")
        bearish = sum(1 for x in kept if x.get("sentiment_label") == "bearish")
        neutral = len(kept) - bullish - bearish
        labeled = bullish + bearish
        ratio = (bullish / labeled) if labeled else None
        # Map labeled bullish ratio to -100..+100; unlabeled-only stays None.
        score = None
        if labeled:
            score = round(((bullish - bearish) / labeled) * 100.0, 1)
        payload["messages"] = kept[:limit]
        payload["summary"] = {
            "message_count": len(kept),
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "bullish_ratio": round(ratio, 3) if ratio is not None else None,
            "score": score,
        }
        if not kept:
            gap = coverage_gap(observed, start_date, end_date, "StockTwits", f"{symbol_key} sentiment")
            payload["coverage_gap"] = gap or (
                f"<StockTwits returned no messages in the last 24h for {st_symbol}>"
            )
            payload["status"] = "empty"
    except Exception as exc:
        payload["status"] = "unavailable"
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["coverage_gap"] = (
            f"<StockTwits unavailable for {start_date}..{end_date}: feed error, "
            f"so this is not an absence of {symbol_key} sentiment>"
        )
    return payload


def fetch_multi(symbols: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
    symbols = symbols or ["BTC", "ETH", "SOL"]
    results = {sym: fetch_symbol_stream(sym, limit=limit) for sym in symbols}
    scores = {
        sym: result["summary"].get("score")
        for sym, result in results.items()
        if result.get("status") == "ok" and result["summary"].get("score") is not None
    }
    aggregate = None
    if scores:
        aggregate = round(sum(scores.values()) / len(scores), 1)
    return {
        "status": "ok" if any(r.get("status") == "ok" for r in results.values()) else "unavailable",
        "aggregate_score": aggregate,
        "symbols": results,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Live-only social sentiment; not point-in-time historical archive.",
    }


if __name__ == "__main__":
    import json

    print(json.dumps(fetch_multi(), indent=2, ensure_ascii=False))
