"""Persistent, append-only v2 run ledger and scorecard (Phase 6).

Design rules:
- Never rewrite past decision rows; only append or fill settlement fields on pending rows.
- Never touch legacy production history writers or Telegram.
- Store under history/intelligence_v2/ so GitHub Actions can persist across runs.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .contracts import (
    DecisionRecord,
    RiskAssessment,
    ShadowRun,
    to_dict,
)
from .evaluation import evaluate_decision

LEDGER_DIRNAME = "intelligence_v2"
RUNS_FILE = "run_ledger.json"
DECISIONS_FILE = "decision_ledger.json"
EVALUATIONS_FILE = "evaluation_ledger.json"
SCORECARD_FILE = "scorecard.json"
META_FILE = "ledger_meta.json"

HORIZONS = (1, 3, 6)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ledger_dir(history_dir: str) -> str:
    path = os.path.join(history_dir, LEDGER_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def _path(history_dir: str, name: str) -> str:
    return os.path.join(ledger_dir(history_dir), name)


def _load_list(history_dir: str, name: str) -> List[dict]:
    try:
        with open(_path(history_dir, name), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _load_dict(history_dir: str, name: str) -> dict:
    try:
        with open(_path(history_dir, name), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_json(history_dir: str, name: str, value: Any) -> None:
    path = _path(history_dir, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _decision_key(symbol: str, run_id: str, horizon: int) -> str:
    return f"{run_id}|{symbol}|h{horizon}"


def append_run(history_dir: str, shadow: ShadowRun) -> dict:
    """Append a shadow run summary and its decisions to the persistent ledger."""
    runs = _load_list(history_dir, RUNS_FILE)
    decisions = _load_list(history_dir, DECISIONS_FILE)

    existing_run_ids = {row.get("run_id") for row in runs}
    if shadow.run_id in existing_run_ids:
        return {"status": "duplicate", "run_id": shadow.run_id, "appended_decisions": 0}

    run_row = {
        "run_id": shadow.run_id,
        "started_at": shadow.started_at,
        "completed_at": shadow.completed_at,
        "timeframe": shadow.timeframe,
        "mode": shadow.mode,
        "symbols": [d.symbol for d in shadow.decisions],
        "decision_count": len(shadow.decisions),
        "safety": shadow.safety,
        "appended_at": _utc_now(),
    }
    runs.append(run_row)

    risks_by_symbol = {r.symbol: r for r in shadow.risk_assessments}
    appended = 0
    for decision in shadow.decisions:
        risk = risks_by_symbol.get(decision.symbol)
        decision_row = {
            "decision_id": f"{shadow.run_id}:{decision.symbol}",
            "run_id": shadow.run_id,
            "symbol": decision.symbol,
            "timeframe": decision.timeframe,
            "decision": decision.decision,
            "signal_direction": decision.signal_direction,
            "gate": decision.gate,
            "confidence": decision.confidence,
            "approval_required": decision.approval_required,
            "execution_allowed": decision.execution_allowed,
            "thesis": decision.thesis,
            "blocking_reasons": decision.blocking_reasons,
            "invalidation": decision.invalidation,
            "paper_conditions": decision.paper_conditions,
            "evaluated_at": decision.evaluated_at,
            "model_version": decision.model_version,
            "risk_overall_level": risk.overall_level if risk else None,
            "risk_veto": risk.veto if risk else None,
            "guarded": bool(
                risk and (risk.veto or risk.overall_level in {"high", "unknown"} or decision.gate == "blocked")
            ),
            "evidence_ids": decision.evidence_ids,
            "horizons": {
                str(h): {"status": "pending", "evaluation": None} for h in HORIZONS
            },
            "appended_at": _utc_now(),
        }
        decisions.append(decision_row)
        appended += 1

    _save_json(history_dir, RUNS_FILE, runs)
    _save_json(history_dir, DECISIONS_FILE, decisions)
    meta = _load_dict(history_dir, META_FILE)
    meta.update({
        "updated_at": _utc_now(),
        "last_run_id": shadow.run_id,
        "total_runs": len(runs),
        "total_decisions": len(decisions),
        "phase": 6,
        "schema_version": 1,
    })
    _save_json(history_dir, META_FILE, meta)
    return {"status": "appended", "run_id": shadow.run_id, "appended_decisions": appended}


def settle_pending(history_dir: str) -> dict:
    """Settle pending horizon evaluations using only post-decision bars."""
    decisions = _load_list(history_dir, DECISIONS_FILE)
    evaluations = _load_list(history_dir, EVALUATIONS_FILE)
    existing_eval_keys = {
        _decision_key(e.get("symbol", ""), e.get("run_id", ""), int(e.get("horizon_bars") or 0))
        for e in evaluations
    }

    newly_settled = 0
    still_pending = 0
    insufficient = 0

    for row in decisions:
        decision = DecisionRecord(
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
            contributing_agents=["ledger"],
            confidence=int(row.get("confidence") or 0),
            evidence_ids=row.get("evidence_ids") or [],
            evaluated_at=row.get("evaluated_at") or row.get("appended_at") or _utc_now(),
            model_version=row.get("model_version") or "ledger",
        )
        risk = RiskAssessment(
            agent="risk",
            run_id=row["run_id"],
            symbol=row["symbol"],
            timeframe=row.get("timeframe") or "4h",
            overall_level=row.get("risk_overall_level") or "unknown",
            downside_risk=row.get("risk_overall_level") or "unknown",
            squeeze_risk="unknown",
            data_risk="unknown",
            veto=bool(row.get("risk_veto")),
            flags=[],
            stress_scenarios=[],
            confidence=int(row.get("confidence") or 0),
            evidence_ids=row.get("evidence_ids") or [],
            evaluated_at=decision.evaluated_at,
            model_version="ledger",
        )
        horizons = row.setdefault("horizons", {})
        for h in HORIZONS:
            key = str(h)
            slot = horizons.get(key) or {"status": "pending", "evaluation": None}
            if slot.get("status") == "settled":
                continue
            record = evaluate_decision(decision, risk, history_dir, h)
            eval_dict = to_dict(record)
            slot["status"] = record.status
            slot["evaluation"] = eval_dict
            slot["updated_at"] = _utc_now()
            horizons[key] = slot

            ek = _decision_key(decision.symbol, decision.run_id, h)
            if record.status == "settled":
                newly_settled += 1
                if ek not in existing_eval_keys:
                    evaluations.append(eval_dict)
                    existing_eval_keys.add(ek)
            elif record.status == "pending":
                still_pending += 1
            else:
                insufficient += 1
        row["horizons"] = horizons
        row["last_settled_at"] = _utc_now()

    _save_json(history_dir, DECISIONS_FILE, decisions)
    _save_json(history_dir, EVALUATIONS_FILE, evaluations)

    scorecard = build_persistent_scorecard(evaluations, decisions)
    _save_json(history_dir, SCORECARD_FILE, scorecard)

    meta = _load_dict(history_dir, META_FILE)
    meta.update({
        "updated_at": _utc_now(),
        "last_settle_at": _utc_now(),
        "newly_settled": newly_settled,
        "still_pending": still_pending,
        "insufficient_data": insufficient,
        "total_evaluation_rows": len(evaluations),
    })
    _save_json(history_dir, META_FILE, meta)

    return {
        "newly_settled": newly_settled,
        "still_pending": still_pending,
        "insufficient_data": insufficient,
        "total_evaluation_rows": len(evaluations),
        "scorecard_summary": {
            "directional_records": scorecard.get("directional_records"),
            "direction_accuracy_pct": scorecard.get("direction_accuracy_pct"),
            "settled_records": scorecard.get("settled_records"),
            "pending_records": scorecard.get("pending_records"),
        },
    }


def build_persistent_scorecard(evaluations: List[dict], decisions: List[dict]) -> dict:
    """Aggregate all settled evaluation rows into a durable scorecard snapshot."""
    settled = [e for e in evaluations if e.get("status") == "settled"]
    pending_slots = 0
    for row in decisions:
        for slot in (row.get("horizons") or {}).values():
            if (slot or {}).get("status") == "pending":
                pending_slots += 1

    directional = [e for e in settled if e.get("direction_correct") is not None]
    hits = [e for e in directional if e.get("direction_correct")]
    guarded = [e for e in settled if e.get("guarded") and e.get("risk_warning_supported") is not None]
    supported = [e for e in guarded if e.get("risk_warning_supported")]

    by_decision: Dict[str, Dict[str, int]] = {}
    by_horizon: Dict[str, Dict[str, Any]] = {}
    for e in evaluations:
        dname = e.get("decision") or "UNKNOWN"
        by_decision.setdefault(dname, {"records": 0, "settled": 0, "pending": 0})
        by_decision[dname]["records"] += 1
        if e.get("status") == "settled":
            by_decision[dname]["settled"] += 1
        elif e.get("status") == "pending":
            by_decision[dname]["pending"] += 1

        h = str(e.get("horizon_bars"))
        by_horizon.setdefault(h, {"settled": 0, "directional": 0, "hits": 0, "accuracy_pct": None})
        if e.get("status") == "settled":
            by_horizon[h]["settled"] += 1
            if e.get("direction_correct") is not None:
                by_horizon[h]["directional"] += 1
                if e.get("direction_correct"):
                    by_horizon[h]["hits"] += 1

    for h, stats in by_horizon.items():
        if stats["directional"]:
            stats["accuracy_pct"] = round(stats["hits"] / stats["directional"] * 100, 2)

    return {
        "agent": "ledger-evaluator",
        "evaluated_at": _utc_now(),
        "horizons": list(HORIZONS),
        "decisions_total": len(decisions),
        "records_total": len(evaluations),
        "settled_records": len(settled),
        "pending_records": pending_slots,
        "directional_records": len(directional),
        "directional_hits": len(hits),
        "direction_accuracy_pct": round(len(hits) / len(directional) * 100, 2) if directional else None,
        "guarded_records": len(guarded),
        "supported_risk_warnings": len(supported),
        "risk_warning_support_pct": round(len(supported) / len(guarded) * 100, 2) if guarded else None,
        "by_decision": by_decision,
        "by_horizon": by_horizon,
        "limitations": [
            "Persistent scorecard aggregates all settled v2 evaluations; pending horizons are excluded.",
            "Does not modify production alerts, Telegram, or legacy history writers.",
            "Direction accuracy is descriptive, not causal proof.",
        ],
        "schema_version": 1,
        "phase": 6,
    }


def process_shadow_run(history_dir: str, shadow: ShadowRun) -> dict:
    """Append this run, settle all pending horizons, return ledger summary."""
    append_info = append_run(history_dir, shadow)
    settle_info = settle_pending(history_dir)
    return {
        "append": append_info,
        "settle": settle_info,
        "ledger_dir": ledger_dir(history_dir),
    }
