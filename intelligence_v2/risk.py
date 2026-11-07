"""Deterministic Risk Agent for v2 shadow mode.

This agent is deliberately conservative: it can raise a veto for an alert or
paper plan, but it cannot place trades and it does not change legacy output.
"""
from __future__ import annotations

from typing import List

from .contracts import AgentOutput, EvidencePack, RiskAssessment, Scenario


def _band(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def evaluate(pack: EvidencePack, technical: AgentOutput) -> RiskAssessment:
    row = pack.observations[0]
    flags: List[str] = []
    reasons: List[str] = []
    downside = 25
    squeeze = 15
    data = 10

    if pack.quality_status == "fail":
        data += 55
        flags.append("failed-quality-gate")
        reasons.append("A required evidence field failed a quality gate.")
    elif pack.quality_status == "warn":
        data += 25
        flags.append("incomplete-evidence")
        reasons.append("Evidence is usable but incomplete.")

    if row.price_change_pct is not None and row.price_change_pct > 2:
        downside += 10
        reasons.append("A fast upward move increases reversal and gap risk.")
    if row.price_change_pct is not None and row.price_change_pct < -2:
        downside += 15
        reasons.append("Recent downside momentum increases continuation risk.")
    if row.oi_change_pct is not None and row.oi_change_pct > 3:
        squeeze += 20
        flags.append("rising-open-interest")
        reasons.append("Rising OI can amplify both liquidation directions.")
    if row.oi_change_pct is not None and row.oi_change_pct < -3:
        flags.append("deleveraging")
        reasons.append("Falling OI suggests position reduction; direction is not identified by OI alone.")
    if row.funding_rate is not None and row.funding_rate > 0.0003:
        downside += 15
        squeeze += 15
        flags.append("crowded-positive-funding")
        reasons.append("Strongly positive funding indicates crowded long exposure.")
    if row.funding_rate is not None and row.funding_rate < -0.0003:
        squeeze += 20
        flags.append("crowded-negative-funding")
        reasons.append("Strongly negative funding indicates crowded short exposure and squeeze risk.")
    if "short-covering-rally" in technical.risk_flags:
        downside += 15
        flags.append("short-covering-rally")
        reasons.append("The move may be driven by short covering rather than durable new demand.")
    if "data-quality-fail" in technical.risk_flags:
        data += 20
        flags.append("technical-data-quality-fail")
    if technical.confidence < 40:
        data += 10
        flags.append("low-technical-confidence")

    downside = min(100, downside)
    squeeze = min(100, squeeze)
    data = min(100, data)
    overall_score = max(downside, squeeze, data)
    overall = _band(overall_score) if pack.quality_status != "fail" else "unknown"
    veto = pack.quality_status == "fail" or data >= 70 or downside >= 80
    stress = [
        Scenario(
            name="sudden-reversal",
            probability_band="high" if downside >= 60 else "medium",
            conditions=["price loses the recent swing level", "volume expands on the decline"],
            invalidation=["price holds the swing level and pullbacks contract"],
        ),
        Scenario(
            name="liquidation-squeeze",
            probability_band="high" if squeeze >= 60 else "medium",
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
        model_version="risk-shadow-v0.1-deterministic",
        reasons=reasons,
    )
