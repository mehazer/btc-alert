"""Canonical contracts for the next-generation intelligence system.

The v2 system exchanges these objects rather than free-form agent messages.
Contracts are dependency-light so they can later move behind an API or a
persistent store without changing agent logic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    venue: str
    dataset: str
    observed_at: str
    retrieved_at: str
    unit: Optional[str] = None
    reliability: str = "unverified"
    locator: Optional[str] = None


@dataclass(frozen=True)
class Observation:
    observation_id: str
    symbol: str
    timeframe: str
    observed_at: str
    price: Optional[float]
    volume: Optional[float]
    open_interest: Optional[float]
    funding_rate: Optional[float]
    rsi: Optional[float]
    price_change_pct: Optional[float]
    oi_change_pct: Optional[float]
    source_refs: List[SourceRef] = field(default_factory=list)


@dataclass(frozen=True)
class QualityGate:
    name: str
    status: str  # pass, warn, fail
    detail: str


@dataclass(frozen=True)
class EvidencePack:
    evidence_id: str
    run_id: str
    symbol: str
    timeframe: str
    created_at: str
    observations: List[Observation]
    quality_gates: List[QualityGate]
    features: Dict[str, Any]
    limitations: List[str]

    @property
    def quality_status(self) -> str:
        statuses = {g.status for g in self.quality_gates}
        return "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"


@dataclass(frozen=True)
class Scenario:
    name: str
    probability_band: str
    conditions: List[str]
    invalidation: List[str]


@dataclass(frozen=True)
class AgentOutput:
    agent: str
    run_id: str
    symbol: str
    timeframe: str
    state: str
    scenarios: List[Scenario]
    risk_flags: List[str]
    confidence: int
    invalidation: List[str]
    evidence_ids: List[str]
    evaluated_at: str
    model_version: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RiskAssessment:
    agent: str
    run_id: str
    symbol: str
    timeframe: str
    overall_level: str  # low, medium, high, unknown
    downside_risk: str
    squeeze_risk: str
    data_risk: str
    veto: bool
    flags: List[str]
    stress_scenarios: List[Scenario]
    confidence: int
    evidence_ids: List[str]
    evaluated_at: str
    model_version: str
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChallengeReport:
    agent: str
    run_id: str
    symbol: str
    timeframe: str
    status: str  # pass, warn, fail
    challenges: List[str]
    missing_evidence: List[str]
    contradictions: List[str]
    required_invalidation: List[str]
    confidence_cap: int
    evidence_ids: List[str]
    evaluated_at: str
    model_version: str


@dataclass(frozen=True)
class ComparisonRecord:
    agent: str
    run_id: str
    symbol: str
    timeframe: str
    compared_at: str
    legacy_timestamp: Optional[str]
    legacy_state: Optional[str]
    v2_state: str
    state_agreement: str  # agree, partial, disagree, unavailable
    legacy_risk: Optional[str]
    v2_risk: str
    risk_agreement: str  # agree, partial, disagree, unavailable
    challenge_status: str
    overall_status: str  # aligned, mixed, divergent, unavailable
    differences: List[str]
    stale_legacy_seconds: Optional[float]
    evidence_ids: List[str]


@dataclass(frozen=True)
class DecisionRecord:
    agent: str
    run_id: str
    symbol: str
    timeframe: str
    decision: str  # OBSERVE, ALERT, PAPER_PLAN
    approval_required: bool
    execution_allowed: bool
    gate: str  # blocked, paper-only, human-approval-required
    thesis: str
    signal_direction: str  # up, down, flat, unknown
    paper_conditions: List[str]
    invalidation: List[str]
    blocking_reasons: List[str]
    contributing_agents: List[str]
    confidence: int
    evidence_ids: List[str]
    evaluated_at: str
    model_version: str


@dataclass(frozen=True)
class EvaluationRecord:
    agent: str
    run_id: str
    symbol: str
    timeframe: str
    decision: str
    signal_direction: str
    horizon_bars: int
    status: str  # settled, pending, insufficient_data
    decision_timestamp: str
    outcome_timestamp: Optional[str]
    forward_return_pct: Optional[float]
    max_favorable_excursion_pct: Optional[float]
    max_adverse_excursion_pct: Optional[float]
    direction_correct: Optional[bool]
    guarded: bool
    risk_warning_supported: Optional[bool]
    confidence: int
    notes: List[str]


@dataclass(frozen=True)
class Scorecard:
    agent: str
    run_id: str
    evaluated_at: str
    horizons: List[int]
    decisions_total: int
    records_total: int
    settled_records: int
    pending_records: int
    directional_records: int
    directional_hits: int
    direction_accuracy_pct: Optional[float]
    guarded_records: int
    supported_risk_warnings: int
    risk_warning_support_pct: Optional[float]
    by_decision: Dict[str, Dict[str, int]]
    limitations: List[str]
    records: List[EvaluationRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ShadowRun:
    run_id: str
    started_at: str
    completed_at: str
    timeframe: str
    mode: str
    evidence_packs: List[EvidencePack]
    agent_outputs: List[AgentOutput]
    risk_assessments: List[RiskAssessment] = field(default_factory=list)
    challenge_reports: List[ChallengeReport] = field(default_factory=list)
    comparisons: List[ComparisonRecord] = field(default_factory=list)
    decisions: List[DecisionRecord] = field(default_factory=list)
    evaluations: List[EvaluationRecord] = field(default_factory=list)
    scorecard: Optional[Scorecard] = None
    safety: Dict[str, Any] = field(default_factory=dict)


def to_dict(value: Any) -> Dict[str, Any]:
    """Convert a contract tree to JSON-compatible dictionaries."""
    return asdict(value)
