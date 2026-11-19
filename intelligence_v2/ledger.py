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

from . import metrics
from .contracts import DecisionRecord, RiskAssessment, ShadowRun, to_dict
from .evaluation import closes_until, evaluate_decision, load_history_rows

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
    scorecard = build_persistent_scorecard(evaluations, decisions, history_dir)
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
            "balanced_accuracy_pct": scorecard.get("balanced_accuracy_pct"),
            "base_rate_up_pct": scorecard.get("base_rate_up_pct"),
            "best_baseline": (scorecard.get("baselines") or {}).get("best_baseline"),
            "model_skill_vs_best_baseline_pct": (scorecard.get("baselines") or {}).get("model_skill_vs_best_baseline_pct"),
            "settled_records": scorecard.get("settled_records"),
            "pending_records": scorecard.get("pending_records"),
        },
        "agent_scorecards_summary": {
            name: {"samples": card.get("samples"), "primary_metric_pct": card.get("primary_metric_pct")}
            for name, card in (agent_cards.get("agents") or {}).items()
        },
    }


def _band_k(neutral_band_k: Any) -> float:
    if neutral_band_k is not None:
        try:
            return float(neutral_band_k)
        except (TypeError, ValueError):
            pass
    try:
        return float(os.environ.get("V2_NEUTRAL_BAND_K", metrics.DEFAULT_NEUTRAL_BAND_K))
    except (TypeError, ValueError):
        return metrics.DEFAULT_NEUTRAL_BAND_K


def _measurement_block(
    evaluations: List[dict],
    decisions: List[dict],
    history_dir: Optional[str],
    neutral_band_k: Any,
) -> dict:
    settled = [e for e in evaluations if e.get("status") == "settled"]
    timestamps = sorted(str(e.get("decision_timestamp")) for e in evaluations if e.get("decision_timestamp"))
    symbols = sorted({e.get("symbol") for e in evaluations if e.get("symbol")})
    directional = [e for e in settled if e.get("direction_correct") is not None]
    neutral = [e for e in settled if e.get("outcome_label") == metrics.NEUTRAL_LABEL]
    unscored = [e for e in settled if e.get("outcome_label") is None and e.get("direction_correct") is None]
    bands = [e.get("neutral_band_pct") for e in settled if e.get("neutral_band_pct") is not None]
    regime: Dict[str, Any] = {}
    if history_dir:
        for symbol in symbols:
            rows = load_history_rows(history_dir, symbol)
            window: List[float] = []
            for row in rows:
                observed = str(row.get("date") or row.get("timestamp") or "")
                price = row.get("price")
                if price is None:
                    continue
                if timestamps and observed and timestamps[0] <= observed <= timestamps[-1]:
                    window.append(float(price))
            regime[symbol] = metrics.regime_summary(window)
    return {
        "labeling": {
            "scheme": "volatility_scaled_neutral_band",
            "band_k": _band_k(neutral_band_k),
            "bands_observed": {"count": len(bands),
                               "median_pct": round(sorted(bands)[len(bands) // 2], 4) if bands else None},
            "basis": "stdev of trailing 20 close-to-close returns, scaled by sqrt(horizon_bars)",
            "atr_note": "History rows carry no high/low, so ATR cannot be computed; close-to-close sigma is used instead.",
        },
        "sample_window": {
            "first_decision_at": timestamps[0] if timestamps else None,
            "last_decision_at": timestamps[-1] if timestamps else None,
        },
        "symbols": symbols,
        "asset_count": len(symbols),
        "horizons": sorted({int(e.get("horizon_bars") or 0) for e in evaluations if e.get("horizon_bars")}),
        "records": len(evaluations),
        "settled": len(settled),
        "directional": len(directional),
        "neutral_outcomes": len(neutral),
        "unscored_outcomes": len(unscored),
        "directional_coverage_pct": round(len(directional) / len(settled) * 100.0, 2) if settled else None,
        "regime": regime,
        "effective_sample_note": (
            "Rows are not independent trials: horizons overlap in time, several decisions share a run, "
            "and all rows fall inside one regime. Treat n as an upper bound on independent evidence."
        ),
        "decisions": len(decisions),
    }


def _baselines_block(evaluations: List[dict], history_dir: Optional[str], neutral_band_k: Any) -> dict:
    rows = [
        e for e in evaluations
        if e.get("status") == "settled"
        and e.get("forward_return_pct") is not None
        and e.get("signal_direction") in metrics.DIRECTIONAL_LABELS
    ]
    if not rows:
        return {"n": 0, "model": {}, "baselines": [], "model_skill_vs_best_baseline_pct": None}
    cache: Dict[str, List[dict]] = {}
    truth: List[Optional[str]] = []
    model: List[Optional[str]] = []
    persistence: List[Optional[str]] = []
    ema_slope: List[Optional[str]] = []
    k = _band_k(neutral_band_k)
    for row in rows:
        symbol = str(row.get("symbol") or "")
        closes: List[float] = []
        if history_dir and symbol:
            if symbol not in cache:
                cache[symbol] = load_history_rows(history_dir, symbol)
            closes = closes_until(cache[symbol], str(row.get("decision_timestamp") or ""))
        band = row.get("neutral_band_pct")
        if band is None and closes:
            band = metrics.neutral_band_pct(
                metrics.volatility_pct(closes), int(row.get("horizon_bars") or 0), k
            )
        truth.append(metrics.label_return(float(row["forward_return_pct"]), band))
        model.append(row.get("signal_direction"))
        naive = metrics.naive_directions(closes) if closes else {}
        persistence.append(naive.get("persistence"))
        ema_slope.append(naive.get("ema_slope"))

    model_stats = metrics.accuracy_stats(truth, model)
    model_stats["name"] = "model"
    baselines = []
    for name, predictions in (
        ("always_up", ["up"] * len(truth)),
        ("always_down", ["down"] * len(truth)),
        ("persistence", persistence),
        ("ema_slope", ema_slope),
    ):
        stats = metrics.accuracy_stats(truth, predictions)
        stats["name"] = name
        stats["skill_vs_model_pct"] = metrics.skill_score(stats.get("accuracy_pct"), model_stats.get("accuracy_pct"))
        baselines.append(stats)
    scored = [b for b in baselines if b.get("accuracy_pct") is not None]
    best = max(scored, key=lambda b: b["accuracy_pct"]) if scored else None
    return {
        "n": model_stats.get("n"),
        "model": model_stats,
        "baselines": baselines,
        "best_baseline": best.get("name") if best else None,
        "model_skill_vs_best_baseline_pct": (
            metrics.skill_score(model_stats.get("accuracy_pct"), best.get("accuracy_pct")) if best else None
        ),
        "note": "Baselines are scored on exactly the rows the model called, against the same banded labels.",
    }


def _confidence_reliability(evaluations: List[dict]) -> dict:
    pairs = [
        (int(e.get("confidence")) / 100.0, bool(e.get("banded_correct")))
        for e in evaluations
        if e.get("status") == "settled"
        and e.get("banded_correct") is not None
        and e.get("confidence") is not None
    ]
    table = metrics.reliability_table(pairs)
    table["source"] = "decision_confidence"
    table["note"] = "Confidence is an integer percentage; it is only meaningful if observed rates track it."
    return table


def _with_banded_labels(evaluations: List[dict], history_dir: Optional[str], neutral_band_k: Any) -> List[dict]:
    """Fill outcome_label and banded correctness for rows settled before this change.

    Stored history is not rewritten: ``direction_correct`` keeps its original
    sign-based meaning so the old number stays comparable, while the banded
    value is carried alongside as ``banded_correct``.
    """
    cache: Dict[str, List[dict]] = {}
    k = _band_k(neutral_band_k)
    enriched: List[dict] = []
    for row in evaluations:
        item = dict(row)
        if item.get("status") != "settled":
            enriched.append(item)
            continue
        symbol = str(item.get("symbol") or "")
        closes: List[float] = []
        if history_dir and symbol:
            if symbol not in cache:
                cache[symbol] = load_history_rows(history_dir, symbol)
            closes = closes_until(cache[symbol], str(item.get("decision_timestamp") or ""))
        band = item.get("neutral_band_pct")
        if band is None and closes:
            band = metrics.neutral_band_pct(
                metrics.volatility_pct(closes), int(item.get("horizon_bars") or 0), k
            )
        label = item.get("outcome_label")
        if label is None:
            label = metrics.label_return(item.get("forward_return_pct"), band)
        item["neutral_band_pct"] = band
        item["outcome_label"] = label
        item["banded_correct"] = metrics.direction_correct(item.get("signal_direction"), label)
        enriched.append(item)
    return enriched


def build_persistent_scorecard(
    evaluations: List[dict],
    decisions: List[dict],
    history_dir: Optional[str] = None,
    neutral_band_k: Any = None,
) -> dict:
    evaluations = _with_banded_labels(evaluations, history_dir, neutral_band_k)
    settled = [e for e in evaluations if e.get("status") == "settled"]
    pending_slots = 0
    for row in decisions:
        for slot in (row.get("horizons") or {}).values():
            if (slot or {}).get("status") == "pending":
                pending_slots += 1
    directional = [e for e in settled if e.get("banded_correct") is not None]
    hits = [e for e in directional if e.get("banded_correct")]
    legacy_directional = [e for e in settled if e.get("direction_correct") is not None]
    legacy_hits = [e for e in legacy_directional if e.get("direction_correct")]
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
            if e.get("banded_correct") is not None:
                by_horizon[h]["directional"] += 1
                if e.get("banded_correct"):
                    by_horizon[h]["hits"] += 1
    for h, stats in by_horizon.items():
        if stats["directional"]:
            stats["accuracy_pct"] = round(stats["hits"] / stats["directional"] * 100, 2)
    measurement = _measurement_block(evaluations, decisions, history_dir, neutral_band_k)
    baselines = _baselines_block(evaluations, history_dir, neutral_band_k) if history_dir else {}
    reliability = _confidence_reliability(evaluations)
    directional_stats = metrics.accuracy_stats(
        [e.get("outcome_label") for e in settled],
        [e.get("signal_direction") for e in settled],
    )
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
        "legacy_direction_accuracy_pct": (
            round(len(legacy_hits) / len(legacy_directional) * 100, 2) if legacy_directional else None
        ),
        "legacy_directional_records": len(legacy_directional),
        "balanced_accuracy_pct": directional_stats.get("balanced_accuracy_pct"),
        "base_rate_up_pct": directional_stats.get("base_rate_up_pct"),
        "neutral_rate_pct": directional_stats.get("neutral_rate_pct"),
        "guarded_records": len(guarded),
        "supported_risk_warnings": len(supported),
        "risk_warning_support_pct": round(len(supported) / len(guarded) * 100, 2) if guarded else None,
        "by_decision": by_decision,
        "by_horizon": by_horizon,
        "measurement": measurement,
        "baselines": baselines,
        "reliability": reliability,
        "limitations": [
            "Persistent scorecard aggregates all settled v2 evaluations; pending horizons are excluded.",
            "Does not modify production alerts, Telegram, or legacy history writers.",
            "Direction accuracy is descriptive, not causal proof.",
            "Accuracy without a baseline cannot separate skill from drift; read baselines before direction_accuracy_pct.",
            "Banded labels exclude moves inside the noise floor, so accuracy is a conditional rate, not full coverage.",
            "Phase 7 horizons include 12 and 24 bars for longer replay.",
        ],
        "schema_version": 3,
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
