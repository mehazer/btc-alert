"""Paper-only Decision Synthesizer for v2 shadow mode.

This module produces structured scenarios, not trading instructions. It has no
broker/exchange client and deliberately marks every non-observe result as
approval-required and execution-disabled.
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
    RiskAssessment,
)


def _directional(state: str) -> bool:
    return state not in {"observe", "neutral", "mixed/inconclusive", "mixed", ""}


def _signal_direction(state: str) -> str:
    if state in {"fresh-long-buildup", "short-covering", "bullish", "overheated"}:
        return "up"
    if state in {"weakening-participation", "bearish", "fear"}:
        return "down"
    return "flat"


def synthesize(
    pack: EvidencePack,
    technical: AgentOutput,
    risk: RiskAssessment,
    challenge: ChallengeReport,
    comparison: ComparisonRecord,
) -> DecisionRecord:
    blockers: List[str] = []
    if pack.quality_status == "fail":
        blockers.append("evidence quality failed")
    if risk.veto:
        blockers.append("risk veto is active")
    if challenge.status == "fail":
        blockers.append("challenge agent found a contradiction")
    comparison_warning = comparison.overall_status == "divergent"

    confidence = min(technical.confidence, risk.confidence, challenge.confidence_cap)
    if comparison.overall_status == "mixed":
        confidence = min(confidence, 60)
    if comparison.overall_status == "unavailable":
        confidence = min(confidence, 50)

    scenario_names = [scenario.name for scenario in technical.scenarios]
    paper_conditions = []
    for scenario in technical.scenarios:
        paper_conditions.extend(scenario.conditions)
    paper_conditions = list(dict.fromkeys(paper_conditions))
    invalidation = list(dict.fromkeys(
        technical.invalidation
        + challenge.required_invalidation
        + [condition for scenario in risk.stress_scenarios for condition in scenario.invalidation]
    ))

    if blockers:
        decision = "OBSERVE"
        gate = "blocked"
        thesis = "Do not form a paper plan until the blocking conditions are resolved."
    elif comparison_warning and _directional(technical.state):
        decision = "ALERT"
        gate = "human-approval-required"
        thesis = "A directional condition exists, but v2 disagrees with the legacy interpretation; review is required."
    elif _directional(technical.state) and risk.overall_level in {"low", "medium"}:
        decision = "PAPER_PLAN"
        gate = "human-approval-required"
        thesis = f"Paper-only scenario review for {technical.state}; no execution is authorized."
    elif _directional(technical.state):
        decision = "ALERT"
        gate = "human-approval-required"
        thesis = "A directional condition exists, but the evidence is not strong enough for a paper plan."
    else:
        decision = "OBSERVE"
        gate = "paper-only"
        thesis = "Evidence does not establish a directional scenario."

    return DecisionRecord(
        agent="decision-synthesizer",
        run_id=pack.run_id,
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        decision=decision,
        approval_required=True,
        execution_allowed=False,
        gate=gate,
        thesis=thesis,
        signal_direction=_signal_direction(technical.state),
        paper_conditions=paper_conditions if decision == "PAPER_PLAN" else [],
        invalidation=invalidation,
        blocking_reasons=blockers,
        contributing_agents=["technical", "risk", "challenge", "comparator"],
        confidence=max(0, min(100, confidence)),
        evidence_ids=sorted(set(
            [pack.evidence_id]
            + technical.evidence_ids
            + risk.evidence_ids
            + challenge.evidence_ids
            + comparison.evidence_ids
        )),
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        model_version="decision-shadow-v0.1-paper-only",
    )
