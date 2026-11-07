"""Paper portfolio agent (Phase 8).

Reads an optional paper portfolio file and evaluates concentration / directional
exposure against current decisions. Never touches exchange keys or live orders.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List

from .contracts import DecisionRecord, PortfolioReport

DEFAULT_PORTFOLIO_PATH = os.path.join("history", "intelligence_v2", "paper_portfolio.json")


def _load_portfolio(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "positions": [],
            "cash_pct": 100.0,
            "note": "No paper_portfolio.json found; portfolio agent runs in empty mode.",
        }


def evaluate(
    run_id: str,
    decisions: List[DecisionRecord],
    portfolio_path: str = DEFAULT_PORTFOLIO_PATH,
) -> PortfolioReport:
    portfolio = _load_portfolio(portfolio_path)
    positions = portfolio.get("positions") or []
    warnings: List[str] = []
    exposures: Dict[str, float] = {}
    total = 0.0
    for pos in positions:
        sym = str(pos.get("symbol") or "").upper()
        weight = float(pos.get("weight_pct") or 0)
        if not sym:
            continue
        exposures[sym] = exposures.get(sym, 0.0) + weight
        total += weight

    if total > 100.5:
        warnings.append(f"Position weights sum to {total:.1f}% (>100%).")
    if total < 0:
        warnings.append("Negative aggregate weight detected.")

    if exposures:
        top_sym, top_w = max(exposures.items(), key=lambda kv: kv[1])
        if top_w >= 40:
            warnings.append(f"High concentration: {top_sym} is {top_w:.1f}% of paper book.")
    else:
        warnings.append("Paper book is empty — no exposure to manage.")

    by_symbol = {d.symbol: d for d in decisions}
    for sym, weight in exposures.items():
        dec = by_symbol.get(sym)
        if not dec:
            warnings.append(f"{sym} is in paper book but has no v2 decision this run.")
            continue
        if dec.decision == "OBSERVE" and weight >= 15:
            warnings.append(f"{sym}: sizeable paper weight while decision is OBSERVE.")
        if dec.gate == "blocked" and weight > 0:
            warnings.append(f"{sym}: risk/challenge blocked decision while paper weight is {weight:.1f}%.")
        if dec.signal_direction == "down" and weight >= 10:
            warnings.append(f"{sym}: paper long weight while signal_direction is down.")

    scenario = (
        "Empty paper portfolio — record notional weights in "
        "history/intelligence_v2/paper_portfolio.json to enable exposure checks."
        if not exposures
        else f"Paper book covers {len(exposures)} symbols, aggregate weight {total:.1f}%."
    )

    return PortfolioReport(
        agent="portfolio",
        run_id=run_id,
        timeframe=decisions[0].timeframe if decisions else "4h",
        positions=positions,
        exposures_pct=exposures,
        aggregate_weight_pct=round(total, 2),
        scenario_summary=scenario,
        warnings=warnings,
        confidence=70 if exposures else 40,
        evidence_ids=[],
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        model_version="portfolio-paper-v0.1",
        execution_allowed=False,
    )
