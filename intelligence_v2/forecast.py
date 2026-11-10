"""Deterministic short-horizon forecast engine (shadow-only).

Design rules:
- Explicit horizons (1, 3, 6, 12 bars)
- Probability *bands*, not point probabilities
- First-class ABSTAIN when evidence is weak or blocked
- Explicit invalidation conditions
- Never enables execution
- Confidence intentionally capped; no fake precision
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .contracts import (
    DecisionRecord,
    EvidencePack,
    ForecastHorizonBand,
    ForecastRecord,
    RiskAssessment,
    AgentOutput,
)

HORIZONS = (1, 3, 6, 12)
MODEL_VERSION = "forecast-engine-v0.2-oi-gated"
MAX_CONFIDENCE = 72

BANDS = {
    "abstain": (0.45, 0.55),
    "low": (0.52, 0.58),
    "medium": (0.58, 0.66),
    "high": (0.66, 0.74),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _forecast_id(run_id: str, symbol: str, timeframe: str) -> str:
    raw = f"{run_id}|{symbol}|{timeframe}|forecast"
    return "fc-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _dir_from_state(state: str) -> str:
    s = (state or "").lower()
    if s in {"fresh-long-buildup", "short-covering", "bullish", "overheated", "spot-led-buying", "cautious-long-bias"}:
        return "up"
    if s in {"weakening-participation", "bearish", "fear", "fresh-short-buildup", "long-liquidation", "soft-selling-drift", "spot-led-selling"}:
        return "down"
    return "flat"


def _confluence(pack: EvidencePack, technical: AgentOutput, risk: RiskAssessment) -> Tuple[str, int, List[str], List[str]]:
    row = pack.observations[0] if pack.observations else None
    drivers: List[str] = []
    conflicts: List[str] = []
    score_up = 0
    score_down = 0

    tech_dir = _dir_from_state(technical.state)
    if tech_dir == "up":
        score_up += 2
        drivers.append(f"Technical state={technical.state}")
    elif tech_dir == "down":
        score_down += 2
        drivers.append(f"Technical state={technical.state}")

    if row and row.price_change_pct is not None:
        if row.price_change_pct > 0.4:
            score_up += 1
            drivers.append(f"Price change {row.price_change_pct:+.2f}%")
        elif row.price_change_pct < -0.4:
            score_down += 1
            drivers.append(f"Price change {row.price_change_pct:+.2f}%")

    if row and row.oi_change_pct is not None:
        if row.price_change_pct is not None and row.price_change_pct > 0 and row.oi_change_pct >= 1.5:
            score_up += 1
            drivers.append(f"OI expanding with price ({row.oi_change_pct:+.1f}%)")
        elif row.price_change_pct is not None and row.price_change_pct < 0 and row.oi_change_pct >= 1.5:
            score_down += 1
            drivers.append(f"OI expanding into decline ({row.oi_change_pct:+.1f}%)")
        elif row.price_change_pct is not None and row.price_change_pct > 0 and row.oi_change_pct <= -1.5:
            conflicts.append("Price up while OI contracts (covering / weak fuel)")
        elif row.price_change_pct is not None and row.price_change_pct < 0 and row.oi_change_pct <= -1.5:
            drivers.append("Price down with OI contraction (deleveraging)")

    if row and row.rsi is not None:
        if row.rsi >= 65:
            drivers.append(f"RSI elevated ({row.rsi:.0f})")
            if tech_dir == "up":
                conflicts.append("Elevated RSI reduces upside continuation confidence")
        elif row.rsi <= 35:
            drivers.append(f"RSI depressed ({row.rsi:.0f})")
            if tech_dir == "down":
                conflicts.append("Depressed RSI reduces downside continuation confidence")

    if risk.overall_level in {"high", "unknown"} or risk.veto:
        conflicts.append(f"Risk level={risk.overall_level}, veto={risk.veto}")

    if score_up >= score_down + 2:
        return "up", score_up, drivers, conflicts
    if score_down >= score_up + 2:
        return "down", score_down, drivers, conflicts
    if score_up == 0 and score_down == 0:
        return "flat", 0, drivers, conflicts
    return "flat", max(score_up, score_down), drivers, conflicts


def _band_for_score(score: int, conflicts: List[str], quality: str) -> str:
    if quality == "fail" or score < 2:
        return "abstain"
    if conflicts and score < 4:
        return "low"
    if score >= 4 and not conflicts and quality == "pass":
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _move_band(direction: str, horizon: int, band: str):
    if direction in {"flat", "abstain"} or band == "abstain":
        return None, None
    base = {1: 0.6, 3: 1.2, 6: 2.0, 12: 3.2}.get(horizon, 1.5)
    if band == "low":
        lo, hi = base * 0.4, base * 1.1
    elif band == "high":
        lo, hi = base * 0.7, base * 1.6
    else:
        lo, hi = base * 0.5, base * 1.3
    if direction == "down":
        return -hi, -lo
    return lo, hi


def _invalidation(direction: str, pack: EvidencePack, technical: AgentOutput) -> List[str]:
    inv = list(technical.invalidation[:3]) if technical.invalidation else []
    inv.append("Evidence quality drops to fail on a subsequent pack")
    if direction == "up":
        inv.append("Price closes below the recent swing support used at issue time")
        inv.append("OI expansion reverses while price stalls")
    elif direction == "down":
        inv.append("Price closes above the recent swing resistance used at issue time")
        inv.append("Short positioning squeezes (price up + OI down sharply)")
    else:
        inv.append("A clear directional break with confirming OI appears")
    out: List[str] = []
    for item in inv:
        if item and item not in out:
            out.append(item)
    return out[:6]


def issue(
    pack: EvidencePack,
    technical: AgentOutput,
    risk: RiskAssessment,
    decision: DecisionRecord,
) -> ForecastRecord:
    abstain_reasons: List[str] = []
    calibration_notes = [
        "Probability bands are conservative ranges, not point estimates.",
        f"Confidence capped at {MAX_CONFIDENCE} until walk-forward calibration is proven.",
        "ABSTAIN is a valid successful outcome when edge is not present.",
        "Directional forecasts require usable OI history (oi_history_ok).",
    ]

    if pack.quality_status == "fail":
        abstain_reasons.append("evidence quality failed")
    oi_gate = next((g for g in pack.quality_gates if g.name == "oi_history_ok"), None)
    row = pack.observations[0] if pack.observations else None
    if oi_gate is not None and oi_gate.status != "pass":
        abstain_reasons.append(f"oi history weak ({oi_gate.detail})")
    if row is not None and row.oi_change_pct is None and row.open_interest is None:
        abstain_reasons.append("open interest unavailable")
    if risk.veto:
        abstain_reasons.append("risk veto active")
    if decision.gate == "blocked":
        abstain_reasons.append("decision gate blocked")
    if decision.decision == "OBSERVE" and decision.blocking_reasons:
        abstain_reasons.append("decision is OBSERVE with blockers")

    direction, score, drivers, conflicts = _confluence(pack, technical, risk)
    for c in conflicts:
        drivers.append(f"Conflict: {c}")

    band = _band_for_score(score, conflicts, pack.quality_status)
    if direction == "flat":
        band = "abstain"
        abstain_reasons.append("no clear directional confluence")

    if band == "abstain" or direction in {"flat", "abstain"} or abstain_reasons:
        stance = "ABSTAIN"
        primary = "abstain"
        band = "abstain"
        p_lo, p_hi = BANDS["abstain"]
        horizons = [
            ForecastHorizonBand(
                horizon_bars=h,
                direction="abstain",
                probability_low=p_lo,
                probability_high=p_hi,
                expected_move_pct_low=None,
                expected_move_pct_high=None,
                status="abstain",
            )
            for h in HORIZONS
        ]
        confidence = min(MAX_CONFIDENCE, max(15, technical.confidence // 3))
        if not abstain_reasons:
            abstain_reasons.append("insufficient directional edge")
    else:
        stance = "FORECAST"
        primary = direction
        p_lo, p_hi = BANDS[band]
        horizons = []
        for h in HORIZONS:
            adj_lo, adj_hi = p_lo, p_hi
            if h >= 6:
                adj_lo = max(0.5, p_lo - 0.03)
                adj_hi = min(0.75, p_hi - 0.02)
            if h >= 12:
                adj_lo = max(0.5, p_lo - 0.05)
                adj_hi = min(0.72, p_hi - 0.04)
            m_lo, m_hi = _move_band(direction, h, band)
            horizons.append(
                ForecastHorizonBand(
                    horizon_bars=h,
                    direction=direction,
                    probability_low=round(adj_lo, 3),
                    probability_high=round(adj_hi, 3),
                    expected_move_pct_low=m_lo,
                    expected_move_pct_high=m_hi,
                    status="active",
                )
            )
        confidence = min(MAX_CONFIDENCE, 40 + score * 6 + (5 if pack.quality_status == "pass" else 0))
        confidence = max(20, confidence - 8 * len(conflicts))

    return ForecastRecord(
        agent="forecast-engine",
        run_id=pack.run_id,
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        forecast_id=_forecast_id(pack.run_id, pack.symbol, pack.timeframe),
        issued_at=_utc_now(),
        stance=stance,
        primary_direction=primary,
        horizons=horizons,
        confidence=int(confidence),
        probability_band=band,
        drivers=drivers[:8],
        invalidation=_invalidation(primary, pack, technical),
        abstain_reasons=abstain_reasons,
        evidence_ids=[pack.evidence_id] + list(technical.evidence_ids)[:6],
        model_version=MODEL_VERSION,
        calibration_notes=calibration_notes,
        execution_allowed=False,
    )
