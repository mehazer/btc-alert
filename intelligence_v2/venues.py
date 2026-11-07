"""Multi-venue public data adapters (Phase 9).

Fetches lightweight cross-venue snapshots for shadow evidence enrichment.
Uses only public HTTP endpoints; no API keys. Failures are non-fatal.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TIMEOUT = 12


def _get_json(url: str) -> Optional[Any]:
    try:
        req = Request(url, headers={"User-Agent": "btc-alert-v2-shadow/1.0"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None


def fetch_binance_ticker(symbol: str) -> Dict[str, Any]:
    data = _get_json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}")
    if not isinstance(data, dict):
        return {"venue": "Binance", "status": "unavailable"}
    return {
        "venue": "Binance",
        "status": "ok",
        "symbol": symbol,
        "last_price": float(data["lastPrice"]) if data.get("lastPrice") else None,
        "volume": float(data["volume"]) if data.get("volume") else None,
        "price_change_pct": float(data["priceChangePercent"]) if data.get("priceChangePercent") else None,
        "dataset": "spot-24hr-ticker",
    }


def fetch_okx_ticker(inst_id: str) -> Dict[str, Any]:
    data = _get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}")
    if not isinstance(data, dict) or not data.get("data"):
        return {"venue": "OKX", "status": "unavailable", "inst_id": inst_id}
    row = data["data"][0]
    return {
        "venue": "OKX",
        "status": "ok",
        "inst_id": inst_id,
        "last_price": float(row["last"]) if row.get("last") else None,
        "volume_24h": float(row["vol24h"]) if row.get("vol24h") else None,
        "dataset": "swap-ticker",
    }


def fetch_okx_open_interest(inst_id: str) -> Dict[str, Any]:
    data = _get_json(f"https://www.okx.com/api/v5/public/open-interest?instId={inst_id}")
    if not isinstance(data, dict) or not data.get("data"):
        return {"venue": "OKX", "status": "unavailable", "dataset": "open-interest", "inst_id": inst_id}
    row = data["data"][0]
    return {
        "venue": "OKX",
        "status": "ok",
        "inst_id": inst_id,
        "open_interest": float(row["oi"]) if row.get("oi") else None,
        "dataset": "open-interest",
    }


def fetch_bybit_ticker(symbol: str) -> Dict[str, Any]:
    data = _get_json(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}")
    if not isinstance(data, dict) or not (data.get("result") or {}).get("list"):
        return {"venue": "Bybit", "status": "unavailable", "symbol": symbol}
    row = data["result"]["list"][0]
    return {
        "venue": "Bybit",
        "status": "ok",
        "symbol": symbol,
        "last_price": float(row["lastPrice"]) if row.get("lastPrice") else None,
        "open_interest": float(row["openInterest"]) if row.get("openInterest") else None,
        "funding_rate": float(row["fundingRate"]) if row.get("fundingRate") else None,
        "dataset": "linear-ticker",
    }


def fetch_coingecko_simple(ids: str = "bitcoin,ethereum,solana") -> Dict[str, Any]:
    data = _get_json(
        f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    )
    if not isinstance(data, dict) or not data:
        return {"venue": "CoinGecko", "status": "unavailable"}
    return {"venue": "CoinGecko", "status": "ok", "dataset": "simple-price", "prices": data}


def _okx_inst(symbol: str) -> str:
    base = symbol.replace("USDT", "")
    return f"{base}-USDT-SWAP"


def snapshot_symbols(symbols: List[str]) -> Dict[str, Any]:
    by_symbol: Dict[str, Any] = {}
    for symbol in symbols:
        venues = [
            fetch_binance_ticker(symbol),
            fetch_okx_ticker(_okx_inst(symbol)),
            fetch_okx_open_interest(_okx_inst(symbol)),
            fetch_bybit_ticker(symbol),
        ]
        prices = [v.get("last_price") for v in venues if v.get("status") == "ok" and v.get("last_price")]
        disagreement = None
        if len(prices) >= 2:
            lo, hi = min(prices), max(prices)
            mid = (lo + hi) / 2 or 1.0
            disagreement = round((hi - lo) / mid * 100, 4)
        by_symbol[symbol] = {
            "venues": venues,
            "price_disagreement_pct": disagreement,
            "venue_count_ok": sum(1 for v in venues if v.get("status") == "ok"),
        }
        time.sleep(0.15)

    cg = fetch_coingecko_simple()
    return {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "symbols": by_symbol,
        "cross_check": cg,
        "notes": [
            "Public endpoints only; keys not required.",
            "Venue disagreement is informational; large spreads can flag stale or broken feeds.",
            "OI units differ by venue and are not always comparable 1:1.",
        ],
    }


def save_snapshot(history_dir: str, snapshot: Dict[str, Any]) -> str:
    path = os.path.join(history_dir, "intelligence_v2", "venue_snapshot.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, ensure_ascii=False)
    return path
