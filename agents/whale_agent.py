"""Whale / on-chain intelligence agent (shadow).

Uses free public endpoints only. Does not send Telegram or place trades
until explicitly integrated and approved.

Free sources used:
- mempool.space API (BTC mempool, fees, some address stats)
- blockchain.info simple stats (fallback)

Paid sources NOT enabled (document only):
- Whale Alert API (commercial)
- CryptoQuant / Glassnode / Arkham (commercial)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

HISTORY_DIR = Path(os.environ.get("HISTORY_DIR", "history"))
OUT_DIR = HISTORY_DIR / "onchain"
TIMEOUT = 20

MEMPOOL = "https://mempool.space/api"
BLOCKCHAIN_INFO = "https://blockchain.info"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str, params: Optional[dict] = None) -> Optional[Any]:
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": "btc-alert-whale/0.1"})
        if not r.ok:
            return None
        return r.json()
    except Exception as e:
        print("whale fetch error", url, e)
        return None


def fetch_mempool_overview() -> Dict[str, Any]:
    """BTC network pressure and fee environment (free)."""
    fees = _get_json(f"{MEMPOOL}/v1/fees/recommended") or {}
    mempool = _get_json(f"{MEMPOOL}/mempool") or {}
    hashrate = _get_json(f"{MEMPOOL}/v1/mining/hashrate/1m") or {}
    return {
        "source": "mempool.space",
        "fees": fees,
        "mempool_tx_count": mempool.get("count") if isinstance(mempool, dict) else None,
        "mempool_vsize": mempool.get("vsize") if isinstance(mempool, dict) else None,
        "hashrate_sample": hashrate if isinstance(hashrate, dict) else None,
    }


def fetch_btc_stats_fallback() -> Dict[str, Any]:
    stats = _get_json(f"{BLOCKCHAIN_INFO}/stats") or {}
    return {
        "source": "blockchain.info",
        "n_tx": stats.get("n_tx"),
        "trade_volume_btc": stats.get("trade_volume_btc"),
        "market_price_usd": stats.get("market_price_usd"),
        "total_btc_sent": stats.get("total_btc_sent"),
    }


def interpret(overview: dict, stats: dict) -> Dict[str, Any]:
    """Conservative, non-directional network read — not a trade signal."""
    fees = overview.get("fees") or {}
    fast = fees.get("fastestFee")
    hour = fees.get("hourFee")
    notes: List[str] = []
    risk = "LOW"
    if isinstance(fast, (int, float)) and fast >= 50:
        risk = "MEDIUM"
        notes.append(f"Elevated fastest fee (~{fast} sat/vB) — network congestion / urgency.")
    if isinstance(fast, (int, float)) and fast >= 100:
        risk = "HIGH"
        notes.append("Very high fees — stress or high demand for block space.")
    if isinstance(hour, (int, float)) and isinstance(fast, (int, float)) and hour and fast / max(hour, 1) >= 3:
        notes.append("Large gap between fastest and hour fee — priority fee pressure.")
    if not notes:
        notes.append("No extreme fee/mempool stress from free endpoints.")
    notes.append("Free APIs do not provide reliable labeled exchange whale flow without paid attribution data.")
    return {
        "network_stress": risk,
        "direction_bias": "Neutral",
        "impact_tier": "LOW" if risk == "LOW" else "MEDIUM",
        "notes": notes,
        "limitations": [
            "No Whale Alert / entity labels on free tier",
            "Cannot reliably distinguish exchange inflow vs internal reshuffle without paid data",
            "Shadow report only — not wired to Telegram by default",
        ],
    }


def run(persist: bool = True) -> Dict[str, Any]:
    overview = fetch_mempool_overview()
    stats = fetch_btc_stats_fallback()
    reading = interpret(overview, stats)
    report = {
        "agent": "whale-onchain",
        "model_version": "whale-shadow-v0.1-free",
        "generated_at": _utc(),
        "execution_allowed": False,
        "telegram_enabled": False,
        "overview": overview,
        "stats_fallback": stats,
        "reading": reading,
        "paid_alternatives_not_enabled": [
            {"name": "Whale Alert API", "why": "Labeled large transfers + exchange attribution", "cost": "Commercial subscription"},
            {"name": "CryptoQuant / Glassnode", "why": "Exchange netflow, whale cohorts", "cost": "Commercial subscription"},
        ],
    }
    if persist:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / "latest_whale_report.json"
        path.write_text(json.dumps(report, indent=2))
        print("wrote", path)
    return report


if __name__ == "__main__":
    r = run(persist=True)
    print(json.dumps({"network_stress": r["reading"]["network_stress"], "notes": r["reading"]["notes"]}, indent=2))
