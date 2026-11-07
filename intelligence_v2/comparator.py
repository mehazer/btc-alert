"""Compare v2 shadow conclusions with the latest legacy mechanism record."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from .contracts import AgentOutput, ChallengeReport, ComparisonRecord, EvidencePack, RiskAssessment


def _load_legacy(path: str, symbol: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            rows = json.load(handle)
        if not isinstance(rows, list):
            return {}
        matching = [row for row in rows if isinstance(row, dict) and row.get("symbol") == symbol]
        return matching[-1] if matching else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _norm_state(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.lower().replace("-", " ").replace("/", " ")
    value = " ".join(value.split())
    aliases = {
        "mixed inconclusive": "mixed",
        "mixed": "mixed",
        "inconclusive": "mixed",
        "weakening participation": "weakening_participation",
        "fresh long buildup": "fresh_long_buildup",
        "short covering": "short_covering",
        "bullish": "bullish",
        "bearish": "bearish",
        "neutral": "neutral",
    }
    return aliases.get(value, value.replace(" ", "_"))


def _norm_risk(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.lower()
    return {"moderate": "medium", "very high": "high"}.get(value, value)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _agreement(left: Optional[str], right: Optional[str]) -> str:
    if left is None or right is None:
        return "unavailable"
    if left == right:
        return "agree"
    broad = {
        "bullish": {"fresh_long_buildup", "short_covering"},
        "fresh_long_buildup": {"bullish", "short_covering"},
        "short_covering": {"bullish", "fresh_long_buildup"},
        "bearish": {"weakening_participation"},
        "weakening_participation": {"bearish"},
    }
    return "partial" if right in broad.get(left, set()) or left in broad.get(right, set()) else "disagree"


def compare(
    pack: EvidencePack,
    technical: AgentOutput,
    risk: RiskAssessment,
    challenge: ChallengeReport,
    mechanism_history_path: str,
) -> ComparisonRecord:
    legacy = _load_legacy(mechanism_history_path, pack.symbol)
    legacy_timestamp = legacy.get("timestamp") or legacy.get("evaluated_at")
    legacy_state = _norm_state(legacy.get("primary_mechanism"))
    v2_state = _norm_state(technical.state) or "observe"
    state_agreement = _agreement(legacy_state, v2_state)
    legacy_risk = _norm_risk(legacy.get("sudden_drop_risk"))
    v2_risk = _norm_risk(risk.overall_level) or "unknown"
    risk_agreement = _agreement(legacy_risk, v2_risk)
    differences = []
    if state_agreement in {"partial", "disagree"}:
        differences.append(f"state: legacy={legacy_state or 'unavailable'} vs v2={v2_state}")
    if risk_agreement in {"partial", "disagree"}:
        differences.append(f"risk: legacy={legacy_risk or 'unavailable'} vs v2={v2_risk}")
    if challenge.status in {"warn", "fail"}:
        differences.append(f"challenge={challenge.status}")
    legacy_dt = _parse_time(legacy_timestamp)
    evidence_dt = _parse_time(pack.observations[0].observed_at)
    stale_seconds = abs((evidence_dt - legacy_dt).total_seconds()) if evidence_dt and legacy_dt else None
    if stale_seconds is not None and stale_seconds > 4 * 3600:
        differences.append(f"legacy evidence is {stale_seconds / 3600:.1f}h from the v2 observation")
    if not legacy:
        overall = "unavailable"
    elif state_agreement == "agree" and risk_agreement == "agree" and challenge.status == "pass":
        overall = "aligned"
    elif state_agreement == "disagree" or risk_agreement == "disagree" or challenge.status == "fail":
        overall = "divergent"
    else:
        overall = "mixed"
    return ComparisonRecord(
        agent="comparator",
        run_id=pack.run_id,
        symbol=pack.symbol,
        timeframe=pack.timeframe,
        compared_at=datetime.now(timezone.utc).isoformat(),
        legacy_timestamp=legacy_timestamp,
        legacy_state=legacy_state,
        v2_state=v2_state,
        state_agreement=state_agreement,
        legacy_risk=legacy_risk,
        v2_risk=v2_risk,
        risk_agreement=risk_agreement,
        challenge_status=challenge.status,
        overall_status=overall,
        differences=differences,
        stale_legacy_seconds=stale_seconds,
        evidence_ids=[pack.evidence_id] + [s.source_id for s in pack.observations[0].source_refs],
    )
