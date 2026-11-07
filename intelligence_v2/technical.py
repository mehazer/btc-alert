"""Shadow Technical Agent adapter.

It reuses the existing deterministic calculations but translates their result
into the new contract. This is intentionally not wired into production alerts.
"""
from __future__ import annotations

import os
from typing import Dict, List

from agents import market_agent, mechanism_interpreter

from .contracts import AgentOutput, EvidencePack, Scenario


def _history(history_dir: str, symbol: str) -> List[dict]:
    import json
    try:
        with open(os.path.join(history_dir, f"{symbol}.json"), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _state(primary: str, market_state: str) -> str:
    if primary and primary != "Mixed / Inconclusive":
        return primary.lower().replace(" ", "-")
    return market_state.lower().replace(" ", "-") or "observe"


def evaluate(
    pack: EvidencePack,
    history_dir: str,
    btc_history: List[dict],
    news_sentiment: float | None = None,
) -> AgentOutput:
    row = pack.observations[0]
    history = _history(history_dir, pack.symbol)
    btc_last = btc_history[-1] if btc_history else {}
    btc_prev = btc_history[-2] if len(btc_history) > 1 else {}
    btc_change = None
    if btc_last.get("price") and btc_prev.get("price"):
        btc_change = (btc_last["price"] - btc_prev["price"]) / btc_prev["price"] * 100
    market = market_agent.evaluate(
        rsi=row.rsi,
        fear_greed_value=None,
        funding_rate=row.funding_rate,
        oi_change_pct=row.oi_change_pct,
        price_change_pct=row.price_change_pct,
        news_sentiment=news_sentiment,
    )
    mechanism = mechanism_interpreter.analyze(
        symbol=pack.symbol,
        history=history,
        current_price=row.price,
        current_oi=row.open_interest,
        current_volume=row.volume,
        current_funding=row.funding_rate,
        current_rsi=row.rsi,
        btc_history=btc_history,
        btc_price_change_pct=btc_change,
        timeframe=pack.timeframe,
    )
    risk = mechanism.get("sudden_drop_risk") or {}
    risk_level = risk.get("level") or "Unknown"
    risk_flags = []
    if pack.quality_status != "pass":
        risk_flags.append(f"data-quality-{pack.quality_status}")
    if risk_level in {"High", "Very High"}:
        risk_flags.append("sudden-drop-risk")
    if mechanism.get("likely_mechanism", {}).get("primary") == "Short covering":
        risk_flags.append("short-covering-rally")
    primary = (mechanism.get("likely_mechanism") or {}).get("primary") or "Observe"
    price_direction = pack.features.get("direction")
    scenarios = [
        Scenario(
            name="continuation",
            probability_band="medium" if price_direction in {"up", "down"} else "low",
            conditions=["price structure remains aligned", "volume does not collapse"],
            invalidation=["price loses the recent swing level"],
        ),
        Scenario(
            name="reversal-or-range",
            probability_band="high" if risk_flags or price_direction == "flat" else "medium",
            conditions=["OI/price relationship weakens", "risk flags remain unresolved"],
            invalidation=["OI and volume confirm the move across the next observation"],
        ),
    ]
    confidence = int(min(80, round((market.get("confidence", 0) + (40 if pack.quality_status == "pass" else 20)) / 2)))
    return AgentOutput(
        agent="technical",
        run_id=pack.run_id,
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        state=_state(primary, market.get("state", "Observe")),
        scenarios=scenarios,
        risk_flags=risk_flags,
        confidence=confidence,
        invalidation=["New data invalidates the evidence pack", "Price breaks the recent swing level"],
        evidence_ids=[pack.evidence_id] + [s.source_id for s in row.source_refs],
        evaluated_at=mechanism.get("evaluated_at") or pack.created_at,
        model_version="technical-shadow-v0.1-adapter",
        inputs={
            "market_agent": market.get("inputs", {}),
            "mechanism": mechanism.get("current_relationship", {}),
            "news_sentiment": news_sentiment,
        },
        reasons=(mechanism.get("likely_mechanism") or {}).get("reasoning", []) + market.get("reasons", [])[:3],
    )
