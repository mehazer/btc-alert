"""Evidence-ID grounded interpreter (Phase 8).

Deterministic by default: synthesizes specialist outputs into a human-readable
narrative that only cites evidence_ids present in the pack / agent outputs.
Optional LLM enrichment is never required; this module does not call external APIs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from .contracts import (
    AgentOutput,
    ChallengeReport,
    ComparisonRecord,
    DecisionRecord,
    EvidencePack,
    InterpretationReport,
    RiskAssessment,
)


def interpret(
    pack: EvidencePack,
    technical: AgentOutput,
    risk: RiskAssessment,
    challenge: ChallengeReport,
    comparison: ComparisonRecord,
    decision: DecisionRecord,
) -> InterpretationReport:
    evidence_ids = sorted(set(
        [pack.evidence_id]
        + list(technical.evidence_ids)
        + list(risk.evidence_ids)
        + list(challenge.evidence_ids)
        + list(comparison.evidence_ids)
        + list(decision.evidence_ids)
    ))

    unsupported: List[str] = []
    if pack.quality_status == "fail":
        unsupported.append("Evidence quality failed; narrative is diagnostic only.")
    if not pack.observations:
        unsupported.append("No observation rows in evidence pack.")

    summary_parts = [
        f"{pack.symbol} on {pack.timeframe}: technical state is '{technical.state}' "
        f"(confidence {technical.confidence}).",
        f"Risk overall={risk.overall_level}, veto={risk.veto}.",
        f"Challenge status={challenge.status}.",
        f"Decision={decision.decision} (gate={decision.gate}, direction={decision.signal_direction}).",
    ]
    if comparison.overall_status in {"divergent", "mixed"}:
        summary_parts.append(
            f"Legacy comparison is {comparison.overall_status}; treat v2 and legacy as not fully aligned."
        )

    bullets: List[str] = []
    for reason in (technical.reasons or [])[:3]:
        bullets.append(f"Technical: {reason}")
    for reason in (risk.reasons or [])[:3]:
        bullets.append(f"Risk: {reason}")
    for item in (challenge.challenges or [])[:3]:
        bullets.append(f"Challenge: {item}")
    for diff in (comparison.differences or [])[:3]:
        bullets.append(f"Comparator: {diff}")
    if decision.blocking_reasons:
        bullets.append("Blocked because: " + "; ".join(decision.blocking_reasons[:3]))
    if decision.thesis:
        bullets.append(f"Decision thesis: {decision.thesis}")

    conflicts: List[str] = []
    if challenge.status == "fail":
        conflicts.extend(challenge.contradictions[:5] or challenge.challenges[:3])
    if risk.veto and decision.signal_direction in {"up", "down"}:
        conflicts.append("Risk veto is active while a directional signal exists.")
    if comparison.state_agreement == "disagree":
        conflicts.append(
            f"Technical state disagrees with legacy ({comparison.legacy_state} vs {comparison.v2_state})."
        )

    unknown: List[str] = list(pack.limitations[:5])
    unknown.extend(challenge.missing_evidence[:5])
    if pack.quality_status != "pass":
        unknown.append(f"Quality status is {pack.quality_status}.")

    return InterpretationReport(
        agent="interpreter",
        run_id=pack.run_id,
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        summary=" ".join(summary_parts),
        bullets=bullets,
        conflicts=conflicts,
        unknowns=unknown,
        evidence_ids=evidence_ids,
        unsupported_claims=unsupported,
        confidence=min(
            technical.confidence,
            risk.confidence,
            challenge.confidence_cap,
            decision.confidence,
        ),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        model_version="interpreter-deterministic-v0.1-evidence-grounded",
    )
