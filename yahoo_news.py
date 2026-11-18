"""Free Yahoo Finance headlines for major crypto tickers.

Uses public Yahoo RSS (no API key). Adapted from TradingAgents yahoo/news
principles:
- Keep only items inside the analysis window
- Empty/unavailable is not "no news" — report coverage gaps
- Live feed only; not a historical archive
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from typing import Any

import requests

from date_window import coverage_gap, filter_items_to_window, recent_window_hours

YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"

DEFAULT_SYMBOLS = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
}


def _parse_items(xml_bytes: bytes, symbol_key: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        pub_el = item.find("pubDate")
        link_el = item.find("link")
        if title_el is None or not (title_el.text or "").strip():
            continue
        title = unescape(title_el.text).strip()
        if len(title) < 12:
            continue
        entry: dict[str, Any] = {
            "source": f"Yahoo Finance ({symbol_key})",
            "type": "yahoo",
            "symbol": symbol_key,
            "headline": title[:280],
        }
        if pub_el is not None and pub_el.text:
            entry["published"] = pub_el.text.strip()
        if link_el is not None and link_el.text:
            entry["link"] = link_el.text.strip()
        items.append(entry)
    return items


def fetch_symbol_news(symbol_key: str = "BTC", limit: int = 8) -> dict[str, Any]:
    yahoo_symbol = DEFAULT_SYMBOLS.get(symbol_key.upper(), f"{symbol_key.upper()}-USD")
    start_dt, end_dt, start_date, end_date = recent_window_hours(24)
    payload: dict[str, Any] = {
        "status": "ok",
        "symbol": symbol_key.upper(),
        "yahoo_symbol": yahoo_symbol,
        "window": {"start": start_date, "end": end_date, "hours": 24},
        "headlines": [],
        "coverage_gap": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        response = requests.get(
            YAHOO_RSS.format(symbol=yahoo_symbol),
            timeout=15,
            headers={"User-Agent": "btc-alert/1.0"},
        )
        response.raise_for_status()
        raw_items = _parse_items(response.content, symbol_key.upper())
        kept, observed = filter_items_to_window(raw_items, start_dt=start_dt, end_dt=end_dt)
        payload["headlines"] = kept[:limit]
        if not kept:
            gap = coverage_gap(observed, start_date, end_date, "Yahoo Finance", f"{symbol_key} news")
            payload["coverage_gap"] = gap or (
                f"<Yahoo Finance returned no headlines in the last 24h for {yahoo_symbol}>"
            )
            payload["status"] = "empty"
    except Exception as exc:
        payload["status"] = "unavailable"
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["coverage_gap"] = (
            f"<Yahoo Finance unavailable for {start_date}..{end_date}: feed error, "
            f"so this is not an absence of {symbol_key} news>"
        )
    return payload


def fetch_multi(symbols: list[str] | None = None, limit: int = 8) -> dict[str, Any]:
    symbols = symbols or ["BTC", "ETH", "SOL"]
    results = {sym: fetch_symbol_news(sym, limit=limit) for sym in symbols}
    headlines: list[dict[str, Any]] = []
    for result in results.values():
        headlines.extend(result.get("headlines") or [])
    return {
        "status": "ok" if any(r.get("status") == "ok" for r in results.values()) else "unavailable",
        "symbols": results,
        "headlines": headlines,
        "headline_count": len(headlines),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Live Yahoo Finance RSS; window-filtered; not a historical archive.",
    }


if __name__ == "__main__":
    import json

    print(json.dumps(fetch_multi(), indent=2, ensure_ascii=False))
