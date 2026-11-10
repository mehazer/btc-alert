"""Persistent, append-only v2 run ledger and scorecards (Phase 6-7).

Design rules:
- Never rewrite past decision identity rows; only fill settlement / horizon slots.
- Never touch legacy production history writers or Telegram.
- Store under history/intelligence_v2/.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from .contracts import DecisionRecord, RiskAssessment, ShadowRun, to_dict
from .evaluation import evaluate_decision

LEDGER_DIRNAME = "intelligence_v2"
RUNS_FILE = "run_ledger.json"
DECISIONS_FILE = "decision_ledger.json"
EVALUATIONS_FILE = "evaluation_ledger.json"
SCORECARD_FILE = "scorecard.json"
AGENT_SCORECARDS_FILE = "agent_scorecards.json"
META_FILE = "ledger_meta.json"
HORIZONS = (1, 3, 6, 12, 24)


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


def _ensure_horizons(row: dict) -> dict:
    horizons = row.setdefault("horizons", {})
    for h in HORIZONS:
        key = str(h)
        if key not in horizons:
            horizons[key] = {"status": "pending", "evaluation": None}
    row["horizons"] = horizons
    return row


def append_run(history_dir: str, shadow: ShadowRun) -> dict:
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
        "phase": 7,
    }
    runs.append(run_row)

    tech_by_symbol = {o.symbol: o for o in shadow.agent_outputs}
    risks_by_symbol = {r.symbol: r for r in shadow.risk_assessments}
    chal_by_symbol = {c.symbol: c for c in shadow.challenge_reports}

    appended = 0
    for decision in shadow.decisions:
        risk = risks_by_symbol.get(decision.symbol)
        tech = tech_by_symbol.get(decision.symbol)
        chal = chal_by_symbol.get(decision.symbol)
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
                risk and (risk.veto or risk.overall_level in {"high", "medium", "unknown"} or decision.gate == "blocked")
            ),
            "evidence_ids": decision.evidence_ids,
            "agents": {
                "technical": {
                    "state": tech.state if tech else None,
                    "confidence": tech.confidence if tech else None,
                    "risk_flags": list(tech.risk_flags) if tech else [],
                    "model_version": tech.model_version if tech else None,
                },
                "risk": {
                    "overall_level": risk.overall_level if risk else None,
                    "veto": risk.veto if risk else None,
                    "downside_risk": risk.downside_risk if risk else None,
                    "squeeze_risk": risk.squeeze_risk if risk else None,
                    "data_risk": risk.data_risk if risk else None,
                    "confidence": risk.confidence if risk else None,
                    "model_version": risk.model_version if risk else None,
                },
                "challenge": {
                    "status": chal.status if chal else None,
                    "challenges": list(chal.challenges) if chal else [],
                    "missing_evidence": list(chal.missing_evidence) if chal else [],
                    "contradictions": list(chal.contradictions) if chal else [],
                    "confidence_cap": chal.confidence_cap if chal else None,
                    "model_version": chal.model_version if chal else None,
                },
            },
            "horizons": {str(h): {"status": "pending", "evaluation": None} for h in HORIZONS},
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
        "phase": 7,
        "schema_version": 2,
        "horizons": list(HORIZONS),
    })
    _save_json(history_dir, META_FILE, meta)
    return {"status": "appended", "run_id": shadow.run_id, "appended_decisions": appended}


def settle_pending(history_dir: str) -> dict:
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
        _ensure_horizons(row)
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
            eval_dict["agents"] = row.get("agents") or {}
            eval_dict["technical_state"] = (row.get("agents") or {}).get("technical", {}).get("state")
            eval_dict["challenge_status"] = (row.get("agents") or {}).get("challenge", {}).get("status")
            eval_dict["risk_veto"] = (row.get("agents") or {}).get("risk", {}).get("veto")
            eval_dict["risk_overall_level"] = (row.get("agents") or {}).get("risk", {}).get("overall_level")
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
    agent_cards = build_agent_scorecards(evaluations, decisions)
    _save_json(history_dir, AGENT_SCORECARDS_FILE, agent_cards)

    meta = _load_dict(history_dir, META_FILE)
    meta.update({
        "updated_at": _utc_now(),
        "last_settle_at": _utc_now(),
        "newly_settled": newly_settled,
        "still_pending": still_pending,
        "insufficient_data": insufficient,
        "total_evaluation_rows": len(evaluations),
        "phase": 7,
        "schema_version": 2,
        "horizons": list(HORIZONS),
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
        "agent_scorecards_summary": {
            name: {"samples": card.get("samples"), "primary_metric_pct": card.get("primary_metric_pct")}
            for name, card in (agent_cards.get("agents") or {}).items()
        },
    }


def build_persistent_scorecard(evaluations: List[dict], decisions: List[dict]) -> dict:
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
            "Phase 7 horizons include 12 and 24 bars for longer replay.",
        ],
        "schema_version": 2,
        "phase": 7,
    }


def build_agent_scorecards(evaluations: List[dict], decisions: List[dict]) -> dict:
    settled = [e for e in evaluations if e.get("status") == "settled"]
    tech_rows = [e for e in settled if e.get("direction_correct") is not None and e.get("signal_direction") in {"up", "down"}]
    tech_hits = [e for e in tech_rows if e.get("direction_correct")]
    by_state: Dict[str, Dict[str, int]] = {}
    for e in tech_rows:
        state = e.get("technical_state") or "unknown"
        by_state.setdefault(state, {"n": 0, "hits": 0})
        by_state[state]["n"] += 1
        if e.get("direction_correct"):
            by_state[state]["hits"] += 1
    for state, stats in by_state.items():
        stats["accuracy_pct"] = round(stats["hits"] / stats["n"] * 100, 2) if stats["n"] else None
    technical = {
        "agent": "technical",
        "samples": len(tech_rows),
        "hits": len(tech_hits),
        "primary_metric": "direction_accuracy_pct",
        "primary_metric_pct": round(len(tech_hits) / len(tech_rows) * 100, 2) if tech_rows else None,
        "by_state": by_state,
        "notes": ["Counts only settled horizons with an up/down signal.", "Grouped by technical.state recorded at decision time."],
    }
    risk_warned = [e for e in settled if e.get("guarded") or e.get("risk_veto") or (e.get("risk_overall_level") in {"high", "medium", "unknown"})]
    risk_supported = [e for e in risk_warned if e.get("risk_warning_supported") is True or (e.get("max_adverse_excursion_pct") is not None and e.get("max_adverse_excursion_pct") <= -1.0)]
    risk_clear = [e for e in settled if not e.get("guarded") and not e.get("risk_veto") and e.get("risk_overall_level") in {"low"}]
    false_calm = [e for e in risk_clear if e.get("max_adverse_excursion_pct") is not None and e.get("max_adverse_excursion_pct") <= -2.0]
    risk_card = {
        "agent": "risk",
        "samples": len(risk_warned),
        "hits": len(risk_supported),
        "primary_metric": "warning_support_pct",
        "primary_metric_pct": round(len(risk_supported) / len(risk_warned) * 100, 2) if risk_warned else None,
        "low_risk_samples": len(risk_clear),
        "false_calm_count": len(false_calm),
        "false_calm_pct": round(len(false_calm) / len(risk_clear) * 100, 2) if risk_clear else None,
        "notes": ["Warning support: guarded/medium/high/veto with >=1% adverse excursion.", "False calm: low risk with later >=2% adverse excursion."],
    }
    chal_fail = [e for e in settled if e.get("challenge_status") == "fail"]
    chal_warn = [e for e in settled if e.get("challenge_status") == "warn"]
    chal_pass = [e for e in settled if e.get("challenge_status") == "pass"]
    fail_useful = [e for e in chal_fail if e.get("direction_correct") is False or (e.get("max_adverse_excursion_pct") is not None and e.get("max_adverse_excursion_pct") <= -1.0)]
    pass_wrong = [e for e in chal_pass if e.get("direction_correct") is False]
    challenge_card = {
        "agent": "challenge",
        "samples": len(chal_fail) + len(chal_warn) + len(chal_pass),
        "fail_samples": len(chal_fail),
        "warn_samples": len(chal_warn),
        "pass_samples": len(chal_pass),
        "hits": len(fail_useful),
        "primary_metric": "fail_useful_pct",
        "primary_metric_pct": round(len(fail_useful) / len(chal_fail) * 100, 2) if chal_fail else None,
        "pass_error_pct": round(len(pass_wrong) / len(chal_pass) * 100, 2) if chal_pass else None,
        "notes": ["fail_useful: challenge=fail and bad outcome.", "pass_error: challenge=pass but direction wrong."],
    }
    return {
        "evaluated_at": _utc_now(),
        "phase": 7,
        "schema_version": 2,
        "horizons": list(HORIZONS),
        "decisions_total": len(decisions),
        "settled_evaluation_rows": len(settled),
        "agents": {"technical": technical, "risk": risk_card, "challenge": challenge_card},
        "limitations": [
            "Agent scorecards require settled horizons; early runs stay empty.",
            "Metrics are descriptive calibration aids.",
        ],
    }


def process_shadow_run(history_dir: str, shadow: ShadowRun) -> dict:
    append_info = append_run(history_dir, shadow)
    settle_info = settle_pending(history_dir)
    return {"append": append_info, "settle": settle_info, "ledger_dir": ledger_dir(history_dir), "phase": 7}
