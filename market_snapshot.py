"""Build history/market.json from live free inputs.

Combines:
- BTC RSI / price / OI / funding from history written by alert_bot
- Fear & Greed (alternative.me)
- News sentiment (agents.news_sentiment)
- StockTwits aggregate (history/latest_stocktwits.json or live fetch)
- Deterministic market_agent evaluation

Missing inputs stay null — never filled with zeros.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from agents import market_agent
from agents import news_sentiment

HISTORY_DIR = Path(os.environ.get("HISTORY_DIR", "history"))
BTC_HISTORY = HISTORY_DIR / "BTCUSDT.json"
MARKET_PATH = HISTORY_DIR / "market.json"
STOCKTWITS_PATH = HISTORY_DIR / "latest_stocktwits.json"
FEAR_GREED_URL = "https://api.alternative.me/fng/"


def _load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _btc_inputs(rows: list) -> dict:
    if not rows:
        return {}
    last = rows[-1] if isinstance(rows, list) else {}
    prev = rows[-2] if isinstance(rows, list) and len(rows) >= 2 else {}
    oi = last.get("open_interest")
    if oi is None:
        oi = last.get("oi")
    prev_oi = prev.get("open_interest")
    if prev_oi is None:
        prev_oi = prev.get("oi")
    oi_change = last.get("oi_change_pct")
    if oi_change is None and oi is not None and prev_oi not in (None, 0):
        oi_change = (float(oi) - float(prev_oi)) / float(prev_oi) * 100.0
    price_change = last.get("price_change_pct")
    if price_change is None:
        p0, p1 = prev.get("price"), last.get("price")
        if p0 not in (None, 0) and p1 is not None:
            price_change = (float(p1) - float(p0)) / float(p0) * 100.0
    funding = last.get("funding_rate")
    if funding is None:
        funding = last.get("funding")
    return {
        "rsi": last.get("rsi"),
        "funding_rate": funding,
        "oi_change_pct": oi_change,
        "price_change_pct": price_change,
        "price": last.get("price"),
        "observed_at": last.get("date") or last.get("retrieved_at"),
    }


def _fear_greed() -> tuple:
    try:
        r = requests.get(FEAR_GREED_URL, params={"limit": 1}, timeout=15)
        r.raise_for_status()
        row = (r.json().get("data") or [{}])[0]
        return int(row.get("value")), row.get("value_classification")
    except Exception as exc:
        print(f"Fear&Greed unavailable: {type(exc).__name__}: {exc}")
        return None, None


def _social_score() -> tuple:
    data = _load_json(STOCKTWITS_PATH, {}) or {}
    score = data.get("aggregate_score")
    status = data.get("status")
    if score is not None:
        return score, status or "ok"
    try:
        import stocktwits_sentiment

        live = stocktwits_sentiment.fetch_multi(["BTC", "ETH", "SOL"], limit=20)
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(STOCKTWITS_PATH, "w", encoding="utf-8") as f:
            json.dump(live, f, indent=2, ensure_ascii=False)
        return live.get("aggregate_score"), live.get("status")
    except Exception as exc:
        print(f"StockTwits unavailable: {type(exc).__name__}: {exc}")
        return None, "unavailable"


def build() -> dict:
    rows = _load_json(BTC_HISTORY, []) or []
    btc = _btc_inputs(rows if isinstance(rows, list) else [])
    fg_val, fg_label = _fear_greed()
    news = news_sentiment.load_and_score(str(HISTORY_DIR))
    social_score, social_status = _social_score()

    evaluation = market_agent.evaluate(
        rsi=btc.get("rsi"),
        fear_greed_value=fg_val,
        funding_rate=btc.get("funding_rate"),
        oi_change_pct=btc.get("oi_change_pct"),
        price_change_pct=btc.get("price_change_pct"),
        news_sentiment=news.get("score"),
        social_sentiment=social_score,
    )

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "fear_greed_value": fg_val,
        "fear_greed_label": fg_label,
        "funding_rate": btc.get("funding_rate"),
        "rsi": btc.get("rsi"),
        "oi_change_pct": btc.get("oi_change_pct"),
        "price_change_pct": btc.get("price_change_pct"),
        "price": btc.get("price"),
        "btc_observed_at": btc.get("observed_at"),
        "news_sentiment": news.get("score"),
        "news_label": news.get("label"),
        "news_macro_score": news.get("macro_score"),
        "social_sentiment": social_score,
        "social_status": social_status,
        "social_source": "StockTwits",
        "market_agent": {
            "state": evaluation.get("state"),
            "bias_score": evaluation.get("bias_score"),
            "intensity": evaluation.get("intensity"),
            "confidence": evaluation.get("confidence"),
            "reasons": evaluation.get("reasons") or [],
            "bullish_points": evaluation.get("bullish_points"),
            "bearish_points": evaluation.get("bearish_points"),
            "evaluated_at": evaluation.get("evaluated_at"),
        },
        "method": "BTC history + F&G + news_sentiment + StockTwits → market_agent",
        "limitations": [],
    }
    if btc.get("rsi") is None:
        payload["limitations"].append("BTC RSI missing from history/BTCUSDT.json")
    if social_score is None:
        payload["limitations"].append("StockTwits social score unavailable")
    if news.get("coverage") == "empty":
        payload["limitations"].append("News sentiment had no usable headlines")
    return payload


def main():
    payload = build()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(MARKET_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    ma = payload.get("market_agent") or {}
    print(
        f"market.json written state={ma.get('state')} bias={ma.get('bias_score')} "
        f"news={payload.get('news_sentiment')} social={payload.get('social_sentiment')} "
        f"confidence={ma.get('confidence')}"
    )
    if payload.get("limitations"):
        print("limitations:", "; ".join(payload["limitations"]))


if __name__ == "__main__":
    main()
