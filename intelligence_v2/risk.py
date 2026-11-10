"""Deterministic Risk Agent for v2 shadow mode.

Conservative but no longer silent: raises medium/high when crowding,
momentum, or data quality warrants it. Still cannot place trades.
"""
from __future__ import annotations

from typing import List

from .contracts import AgentOutput, EvidencePack, RiskAssessment, Scenario


def _band(score: int) -> str:
    if score >= 60:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def evaluate(pack: EvidencePack, technical: AgentOutput) -> RiskAssessment:
    row = pack.observations[0]
    flags: List[str] = []
    reasons: List[str] = []
    downside = 20
    squeeze = 15
    data = 10

    if pack.quality_status == "fail":
        data += 55
        flags.append("failed-quality-gate")
        reasons.append("A required evidence field failed a quality gate.")
    elif pack.quality_status == "warn":
        data += 20
        flags.append("incomplete-evidence")
        reasons.append("Evidence is usable but incomplete.")

    if row.price_change_pct is not None and row.price_change_pct > 1.2:
        downside += 12
        reasons.append("A fast upward move increases reversal and gap risk.")
    if row.price_change_pct is not None and row.price_change_pct > 2.5:
        downside += 10
        flags.append("sharp-up-move")
    if row.price_change_pct is not None and row.price_change_pct < -1.2:
        downside += 15
        reasons.append("Recent downside momentum increases continuation risk.")
    if row.price_change_pct is not None and row.price_change_pct < -2.5:
        downside += 10
        flags.append("sharp-down-move")

    if row.oi_change_pct is not None and row.oi_change_pct > 2.0:
        squeeze += 18
        flags.append("rising-open-interest")
        reasons.append("Rising OI can amplify both liquidation directions.")
    if row.oi_change_pct is not None and row.oi_change_pct > 5.0:
        squeeze += 12
        flags.append("oi-spike")
    if row.oi_change_pct is not None and row.oi_change_pct < -2.0:
        flags.append("deleveraging")
        reasons.append("Falling OI suggests position reduction; direction is not identified by OI alone.")
        downside += 8

    if row.funding_rate is not None and row.funding_rate > 0.0002:
        downside += 12
        squeeze += 12
        flags.append("crowded-positive-funding")
        reasons.append("Positive funding indicates crowded long exposure.")
    if row.funding_rate is not None and row.funding_rate > 0.0004:
        downside += 10
        flags.append("extreme-positive-funding")
    if row.funding_rate is not None and row.funding_rate < -0.0002:
        squeeze += 18
        flags.append("crowded-negative-funding")
        reasons.append("Negative funding indicates crowded short exposure and squeeze risk.")
    if row.funding_rate is not None and row.funding_rate < -0.0004:
        squeeze += 10
        flags.append("extreme-negative-funding")

    if row.rsi is not None and row.rsi >= 70:
        downside += 12
        flags.append("rsi-overbought")
        reasons.append("Elevated RSI increases pullback risk after extended upside.")
    if row.rsi is not None and row.rsi <= 30:
        downside += 10
        flags.append("rsi-oversold")
        reasons.append("Depressed RSI can mean forced selling or snapback risk.")

    tech_flags = set(technical.risk_flags or [])
    if "short-covering-rally" in tech_flags or technical.state in {"short-covering", "weakening-participation"}:
        downside += 12
        flags.append("fragile-rally-or-weak-participation")
        reasons.append("Technical state suggests the move may lack durable new demand.")
    if "data-quality-fail" in tech_flags:
        data += 20
        flags.append("technical-data-quality-fail")
    if technical.confidence < 40:
        data += 10
        flags.append("low-technical-confidence")

    oi_gate = next((g for g in pack.quality_gates if g.name == "oi_history_ok"), None)
    if oi_gate is not None and oi_gate.status != "pass":
        data += 15
        flags.append("weak-oi-history")
        reasons.append("OI history is thin; risk estimates are less reliable.")

    downside = min(100, downside)
    squeeze = min(100, squeeze)
    data = min(100, data)
    overall_score = max(downside, squeeze, data)
    overall = _band(overall_score) if pack.quality_status != "fail" else "unknown"
    veto = pack.quality_status == "fail" or data >= 70 or downside >= 75 or squeeze >= 80

    stress = [
        Scenario(
            name="sudden-reversal",
            probability_band="high" if downside >= 55 else "medium",
            conditions=["price loses the recent swing level", "volume expands on the decline"],
            invalidation=["price holds the swing level and pullbacks contract"],
        ),
        Scenario(
            name="liquidation-squeeze",
            probability_band="high" if squeeze >= 55 else "medium",
            conditions=["OI expands while price stalls", "funding becomes extreme"],
            invalidation=["OI declines gradually with stable funding"],
        ),
    ]
    confidence = max(0, min(90, 100 - data // 2))
    return RiskAssessment(
        agent="risk",
        run_id=pack.run_id,
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        overall_level=overall,
        downside_risk=_band(downside),
        squeeze_risk=_band(squeeze),
        data_risk=_band(data),
        veto=veto,
        flags=sorted(set(flags)),
        stress_scenarios=stress,
        confidence=confidence,
        evidence_ids=[pack.evidence_id] + [s.source_id for s in row.source_refs],
        evaluated_at=pack.created_at,
        model_version="risk-shadow-v0.2-sensitive",
        reasons=reasons,
    )
