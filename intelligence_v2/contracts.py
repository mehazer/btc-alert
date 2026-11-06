"""Canonical contracts for the next-generation intelligence system.

The v2 system exchanges these objects rather than free-form agent messages.
Contracts are deliberately dependency-light so they can later be moved behind
an API or a persistent store without changing agent logic.
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
class ShadowRun:
    run_id: str
    started_at: str
    completed_at: str
    timeframe: str
    mode: str
    evidence_packs: List[EvidencePack]
    agent_outputs: List[AgentOutput]
    safety: Dict[str, Any]


def to_dict(value: Any) -> Dict[str, Any]:
    """Convert a contract tree to JSON-compatible dictionaries."""
    return asdict(value)
