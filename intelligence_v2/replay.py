"""Read-only historical settlement replay for the v2 ledger.

This module intentionally does not modify ``history/intelligence_v2`` live
ledger files. It evaluates the existing decision identity rows against the
historical market observations available at decision time and writes a
separate, provenance-labelled replay batch.

Usage:
    python -m intelligence_v2.replay --history-dir history
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List

from .contracts import DecisionRecord, RiskAssessment, to_dict
from .evaluation import evaluate_decision
from .ledger import HORIZONS, build_agent_scorecards, build_persistent_scorecard


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _decision_record(row: dict) -> DecisionRecord:
    return DecisionRecord(
        agent="decision-synthesizer",
        run_id=row["run_id"],
        symbol=row["symbol"],
        timeframe=row.get("timeframe") or "4h",
        decision=row.get("decision") or "OBSERVE",
        approval_required=bool(row.get("approval_required", True)),
        execution_allowed=bool(row.get("execution_allowed", False)),
        gate=row.get("gate") or "paper-only",
        thesis=row.get("thesis") or "",
        signal_direction=row.get("signal_direction") or "flat",
        paper_conditions=row.get("paper_conditions") or [],
        invalidation=row.get("invalidation") or [],
        blocking_reasons=row.get("blocking_reasons") or [],
        contributing_agents=["replay"],
        confidence=int(row.get("confidence") or 0),
        evidence_ids=row.get("evidence_ids") or [],
        evaluated_at=row.get("evaluated_at") or row.get("appended_at") or _now(),
        model_version=row.get("model_version") or "ledger",
    )


def _risk_assessment(row: dict, decision: DecisionRecord) -> RiskAssessment:
    risk = row.get("agents", {}).get("risk", {}) if isinstance(row.get("agents"), dict) else {}
    overall = row.get("risk_overall_level") or risk.get("overall_level") or "unknown"
    return RiskAssessment(
        agent="risk",
        run_id=row["run_id"],
        symbol=row["symbol"],
        timeframe=decision.timeframe,
        overall_level=overall,
        downside_risk=risk.get("downside_risk") or overall,
        squeeze_risk=risk.get("squeeze_risk") or "unknown",
        data_risk=risk.get("data_risk") or "unknown",
        veto=bool(row.get("risk_veto", risk.get("veto", False))),
        flags=[],
        stress_scenarios=[],
        confidence=int(risk.get("confidence") or row.get("confidence") or 0),
        evidence_ids=row.get("evidence_ids") or [],
        evaluated_at=decision.evaluated_at,
        model_version=risk.get("model_version") or "ledger",
    )


def _source_digest(history_dir: str, decisions: List[dict]) -> str:
    files = []
    for symbol in sorted({row.get("symbol") for row in decisions if row.get("symbol")}):
        path = os.path.join(history_dir, f"{symbol}.json")
        try:
            with open(path, "rb") as handle:
                files.append(handle.read())
        except OSError:
            files.append(f"missing:{symbol}".encode())
    return hashlib.sha256(b"".join(files)).hexdigest()


def _replay_scorecard(evaluations: List[dict], decisions: List[dict]) -> tuple[dict, dict]:
    scorecard = build_persistent_scorecard(evaluations, decisions)
    agent_cards = build_agent_scorecards(evaluations, decisions)
    for payload in (scorecard, agent_cards):
        payload["evaluation_mode"] = "historical_replay"
    return scorecard, agent_cards


def run(history_dir: str, output_dir: str | None = None) -> dict:
    ledger_dir = os.path.join(history_dir, "intelligence_v2")
    decisions = _read_json(os.path.join(ledger_dir, "decision_ledger.json"), [])
    if not isinstance(decisions, list):
        decisions = []
    batch_id = f"replay-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    output_dir = output_dir or os.path.join(ledger_dir, "replay", batch_id)
    started = _now()
    evaluations: List[dict] = []
    replay_decisions = deepcopy(decisions)
    status_counts = {"settled": 0, "pending": 0, "insufficient_data": 0}

    for row in replay_decisions:
        decision = _decision_record(row)
        risk = _risk_assessment(row, decision)
        row["evaluation_mode"] = "historical_replay"
        row["replay_batch_id"] = batch_id
        horizons = row.setdefault("horizons", {})
        for horizon in HORIZONS:
            record = evaluate_decision(decision, risk, history_dir, horizon)
            evaluation = to_dict(record)
            evaluation.update({
                "evaluation_mode": "historical_replay",
                "replay_batch_id": batch_id,
                "lookahead_safe": True,
                "source_decision_id": row.get("decision_id"),
            })
            evaluations.append(evaluation)
            status_counts[record.status] = status_counts.get(record.status, 0) + 1
            horizons[str(horizon)] = {
                "status": record.status,
                "evaluation": evaluation,
                "updated_at": _now(),
                "evaluation_mode": "historical_replay",
                "replay_batch_id": batch_id,
            }

    scorecard, agent_cards = _replay_scorecard(evaluations, replay_decisions)
    live_scorecard = _read_json(os.path.join(ledger_dir, "scorecard.json"), {})
    report = {
        "replay_batch_id": batch_id,
        "evaluation_mode": "historical_replay",
        "lookahead_safe": True,
        "started_at": started,
        "completed_at": _now(),
        "source": {
            "decision_ledger": os.path.join(ledger_dir, "decision_ledger.json"),
            "market_history_dir": history_dir,
            "decision_count": len(decisions),
            "source_history_sha256": _source_digest(history_dir, decisions),
        },
        "coverage": status_counts,
        "live_scorecard_before_replay": {
            "settled_records": live_scorecard.get("settled_records"),
            "pending_records": live_scorecard.get("pending_records"),
            "directional_records": live_scorecard.get("directional_records"),
            "direction_accuracy_pct": live_scorecard.get("direction_accuracy_pct"),
        },
        "replay_scorecard": {
            "settled_records": scorecard.get("settled_records"),
            "pending_records": scorecard.get("pending_records"),
            "directional_records": scorecard.get("directional_records"),
            "direction_accuracy_pct": scorecard.get("direction_accuracy_pct"),
            "by_horizon": scorecard.get("by_horizon", {}),
        },
        "improvement": {
            "additional_settled_records": scorecard.get("settled_records", 0) - (live_scorecard.get("settled_records") or 0),
            "additional_directional_records": scorecard.get("directional_records", 0) - (live_scorecard.get("directional_records") or 0),
            "live_ledger_modified": False,
            "promotion_scorecard_modified": False,
        },
        "limitations": [
            "Replay settles existing v2 decisions only; it does not regenerate historical agent decisions.",
            "Replay results are diagnostic and must not be mixed into the live promotion scorecard.",
            "Historical outcomes do not prove causality or future performance.",
        ],
    }
    _write_json(os.path.join(output_dir, "decision_ledger_replay.json"), replay_decisions)
    _write_json(os.path.join(output_dir, "evaluation_ledger_replay.json"), evaluations)
    _write_json(os.path.join(output_dir, "scorecard_replay.json"), scorecard)
    _write_json(os.path.join(output_dir, "agent_scorecards_replay.json"), agent_cards)
    _write_json(os.path.join(output_dir, "replay_report.json"), report)
    latest_dir = os.path.join(ledger_dir, "replay", "latest")
    for name, value in {
        "replay_report.json": report,
        "scorecard_replay.json": scorecard,
        "agent_scorecards_replay.json": agent_cards,
    }.items():
        _write_json(os.path.join(latest_dir, name), value)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only historical settlement replay for v2")
    parser.add_argument("--history-dir", default="history")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    report = run(args.history_dir, args.output_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
