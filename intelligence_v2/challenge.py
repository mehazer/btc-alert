"""Challenge Agent for v2 shadow mode.

The Challenge Agent does not predict price. It tests whether another agent's
conclusion is sufficiently supported to be trusted or promoted.
"""
from __future__ import annotations

from .contracts import AgentOutput, ChallengeReport, EvidencePack, RiskAssessment


def evaluate(pack: EvidencePack, technical: AgentOutput, risk: RiskAssessment) -> ChallengeReport:
    challenges = []
    missing = []
    contradictions = []
    invalidation = list(technical.invalidation)
    confidence_cap = 100

    if pack.quality_status == "fail":
        challenges.append("Evidence pack contains a failed quality gate.")
        confidence_cap = min(confidence_cap, 30)
    elif pack.quality_status == "warn":
        challenges.append("Evidence pack contains warnings that limit certainty.")
        confidence_cap = min(confidence_cap, 65)

    row = pack.observations[0]
    if row.open_interest is not None and row.oi_change_pct is None:
        missing.append("OI change baseline")
        challenges.append("Current OI exists, but its change cannot be verified.")
    if row.open_interest is not None:
        challenges.append("OI does not identify long versus short direction without additional positioning evidence.")
        confidence_cap = min(confidence_cap, 75)
    if len(row.source_refs) < 2:
        missing.append("independent venue confirmation")
        challenges.append("The conclusion lacks independent multi-venue confirmation.")
        confidence_cap = min(confidence_cap, 60)
    if any("liquidation" in limitation.lower() for limitation in pack.limitations):
        missing.append("liquidation feed")
    if any("derivatives volume" in limitation.lower() for limitation in pack.limitations):
        missing.append("derivatives volume")

    if technical.confidence > confidence_cap:
        contradictions.append(f"Technical confidence {technical.confidence} exceeds challenge cap {confidence_cap}.")
    if risk.veto and technical.state not in {"observe", "neutral", "mixed/inconclusive"}:
        contradictions.append("Technical directional state conflicts with the Risk Agent veto.")
    if "short-covering-rally" in technical.risk_flags and technical.state in {"fresh-long-buildup", "bullish"}:
        contradictions.append("Technical state implies accumulation while a short-covering flag is present.")
    if risk.overall_level == "unknown":
        challenges.append("Risk cannot be classified reliably because the evidence quality failed.")

    if contradictions:
        status = "fail"
    elif challenges or missing:
        status = "warn"
    else:
        status = "pass"
    if status == "fail":
        confidence_cap = min(confidence_cap, 40)

    return ChallengeReport(
        agent="challenge",
        run_id=pack.run_id,
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        status=status,
        challenges=sorted(set(challenges)),
        missing_evidence=sorted(set(missing)),
        contradictions=sorted(set(contradictions)),
        required_invalidation=sorted(set(invalidation + ["Evidence quality must remain valid", "Risk veto must be resolved"])),
        confidence_cap=confidence_cap,
        evidence_ids=[pack.evidence_id] + [s.source_id for s in row.source_refs],
        evaluated_at=pack.created_at,
        model_version="challenge-shadow-v0.1-deterministic",
    )
