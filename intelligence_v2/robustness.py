"""Synthetic historical news and venue robustness simulation.

This is not a claim that synthetic news is real historical news. It is a
controlled sensitivity test: the same point-in-time market history is replayed
under deterministic sentiment-shift and venue-quality scenarios. Results are
isolated from the live ledger and promotion gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from . import challenge, comparator, decision, evidence, risk, technical
from .contracts import QualityGate, to_dict
from .evaluation import evaluate_decision
from .ledger import HORIZONS, build_agent_scorecards, build_persistent_scorecard
from .walkforward import (
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAME,
    MIN_WARMUP_BARS,
    _filter_mechanism,
    _load_histories,
    _parse_time,
    _read_json,
    _write_history,
    _write_json,
)

SCENARIOS = (
    "baseline_no_synthetic_context",
    "bullish_news_shift",
    "bearish_news_shift",
    "venue_disagreement",
    "combined_bearish_news_venue_failure",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scenario_context(name: str, index: int) -> dict:
    phase = index % 24
    news = None
    venue_mode = "unmodeled"
    if name == "bullish_news_shift":
        news = 75.0 if 8 <= phase <= 11 else None
    elif name == "bearish_news_shift":
        news = -75.0 if 8 <= phase <= 11 else None
    elif name == "venue_disagreement":
        venue_mode = "disagreement"
    elif name == "combined_bearish_news_venue_failure":
        news = -75.0 if 8 <= phase <= 11 else None
        venue_mode = "unavailable"
    return {
        "news_sentiment": news,
        "news_source": "synthetic_controlled_shift" if news is not None else "none",
        "venue_mode": venue_mode,
        "venue_spread_pct": 1.25 if venue_mode == "disagreement" else None,
        "venue_snapshot_source": "synthetic_controlled_snapshot" if venue_mode != "unmodeled" else "none",
    }


def _apply_context(pack, context: dict):
    if context["venue_mode"] == "unmodeled":
        return pack
    gates = list(pack.quality_gates)
    status = "warn" if context["venue_mode"] == "disagreement" else "fail"
    gates.append(QualityGate(
        "simulated_venue_consistency",
        status,
        f"synthetic mode={context['venue_mode']} spread_pct={context['venue_spread_pct']}",
    ))
    features = dict(pack.features)
    features["simulated_news_sentiment"] = context["news_sentiment"]
    features["simulated_venue"] = {
        "mode": context["venue_mode"],
        "price_disagreement_pct": context["venue_spread_pct"],
        "source": context["venue_snapshot_source"],
    }
    limitations = list(pack.limitations) + [
        "Historical news and venue state are synthetic controlled inputs, not reconstructed real observations."
    ]
    return replace(pack, quality_gates=gates, features=features, limitations=limitations)


def _decision_row(pack, tech, risk_output, challenge_output, comparison_record, synthesized, context, scenario):
    return {
        "decision_id": f"{synthesized.run_id}:{synthesized.symbol}",
        "run_id": synthesized.run_id,
        "symbol": synthesized.symbol,
        "timeframe": synthesized.timeframe,
        "decision": synthesized.decision,
        "signal_direction": synthesized.signal_direction,
        "gate": synthesized.gate,
        "confidence": synthesized.confidence,
        "approval_required": synthesized.approval_required,
        "execution_allowed": synthesized.execution_allowed,
        "thesis": synthesized.thesis,
        "blocking_reasons": synthesized.blocking_reasons,
        "invalidation": synthesized.invalidation,
        "paper_conditions": synthesized.paper_conditions,
        "evaluated_at": synthesized.evaluated_at,
        "model_version": synthesized.model_version,
        "evaluation_mode": "robustness_simulation",
        "scenario": scenario,
        "lookahead_safe": True,
        "simulated_context": context,
        "source_observed_at": pack.observations[0].observed_at,
        "risk_overall_level": risk_output.overall_level,
        "risk_veto": risk_output.veto,
        "guarded": bool(risk_output.veto or risk_output.overall_level in {"high", "unknown"} or synthesized.gate == "blocked"),
        "evidence_ids": synthesized.evidence_ids,
        "agents": {
            "technical": {"state": tech.state, "confidence": tech.confidence, "risk_flags": tech.risk_flags, "model_version": tech.model_version},
            "risk": {"overall_level": risk_output.overall_level, "veto": risk_output.veto, "downside_risk": risk_output.downside_risk, "squeeze_risk": risk_output.squeeze_risk, "data_risk": risk_output.data_risk, "confidence": risk_output.confidence, "model_version": risk_output.model_version},
            "challenge": {"status": challenge_output.status, "challenges": challenge_output.challenges, "missing_evidence": challenge_output.missing_evidence, "contradictions": challenge_output.contradictions, "confidence_cap": challenge_output.confidence_cap, "model_version": challenge_output.model_version},
            "comparison": {"overall_status": comparison_record.overall_status, "state_agreement": comparison_record.state_agreement, "risk_agreement": comparison_record.risk_agreement},
        },
        "horizons": {},
    }


def _output_hash(payload: dict) -> str:
    clean = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(clean.encode()).hexdigest()


def _scenario_summary(decisions: List[dict], evaluations: List[dict]) -> dict:
    score = build_persistent_scorecard(evaluations, decisions)
    agent_cards = build_agent_scorecards(evaluations, decisions)
    agent_cards.pop("evaluated_at", None)
    confidence_values = [int(row.get("confidence") or 0) for row in decisions]
    risk_vetoes = sum(1 for row in decisions if row.get("risk_veto"))
    challenge_statuses: Dict[str, int] = {}
    for row in decisions:
        status = ((row.get("agents") or {}).get("challenge") or {}).get("status") or "unknown"
        challenge_statuses[status] = challenge_statuses.get(status, 0) + 1
    changed_context = sum(1 for row in decisions if row.get("simulated_context", {}).get("news_sentiment") is not None or row.get("simulated_context", {}).get("venue_mode") != "unmodeled")
    return {
        "decisions": len(decisions),
        "evaluations": len(evaluations),
        "context_injected_decisions": changed_context,
        "settled_records": score.get("settled_records"),
        "pending_records": score.get("pending_records"),
        "directional_records": score.get("directional_records"),
        "directional_hits": score.get("directional_hits"),
        "direction_accuracy_pct": score.get("direction_accuracy_pct"),
        "mean_decision_confidence": round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else None,
        "risk_veto_count": risk_vetoes,
        "challenge_statuses": challenge_statuses,
        "by_horizon": score.get("by_horizon", {}),
        "by_decision": score.get("by_decision", {}),
        "agent_scorecards": agent_cards,
    }, score


def run(history_dir: str, symbols: Iterable[str] = DEFAULT_SYMBOLS, timeframe: str = DEFAULT_TIMEFRAME, output_dir: str | None = None) -> dict:
    symbols = tuple(symbols)
    histories = _load_histories(history_dir, symbols)
    lengths = {symbol: len(rows) for symbol, rows in histories.items()}
    common_bars = min(lengths.values()) if lengths else 0
    scenario_key = json.dumps({"histories": histories, "symbols": symbols, "timeframe": timeframe, "scenarios": SCENARIOS}, sort_keys=True, separators=(",", ":"))
    batch_id = f"robustness-{hashlib.sha256(scenario_key.encode()).hexdigest()[:12]}"
    root = os.path.join(history_dir, "intelligence_v2", "robustness")
    output_dir = output_dir or os.path.join(root, batch_id)
    legacy = _read_json(os.path.join(history_dir, "mechanism_history.json"), [])
    legacy = legacy if isinstance(legacy, list) else []
    scenario_summaries: Dict[str, dict] = {}
    scenario_scores: Dict[str, dict] = {}
    scenario_examples: Dict[str, list] = {}
    leakage_violations: List[str] = []
    all_decisions: Dict[str, List[dict]] = {}

    with tempfile.TemporaryDirectory(prefix="crypto-watch-robustness-") as temp:
        full_dir = os.path.join(temp, "full")
        prefix_dir = os.path.join(temp, "prefix")
        _write_history(full_dir, histories)
        for scenario in SCENARIOS:
            decisions: List[dict] = []
            evaluations: List[dict] = []
            examples: List[dict] = []
            with tempfile.TemporaryDirectory(prefix=f"scenario-{scenario}-", dir=temp) as scenario_temp:
                for index in range(MIN_WARMUP_BARS, common_bars):
                    context = _scenario_context(scenario, index)
                    prefix = {symbol: rows[: index + 1] for symbol, rows in histories.items()}
                    _write_history(prefix_dir, prefix)
                    btc_history = prefix.get("BTCUSDT", [])
                    for symbol in symbols:
                        observed_at = histories[symbol][index]["date"]
                        run_id = f"{batch_id}:{scenario}:{index:04d}:{symbol}"
                        pack = evidence.build_evidence_pack(prefix_dir, symbol, timeframe, run_id)
                        pack = replace(pack, created_at=observed_at)
                        pack = _apply_context(pack, context)
                        tech = technical.evaluate(pack, prefix_dir, btc_history, news_sentiment=context["news_sentiment"])
                        tech = replace(tech, evaluated_at=observed_at)
                        risk_output = risk.evaluate(pack, tech)
                        risk_output = replace(risk_output, evaluated_at=observed_at)
                        challenge_output = challenge.evaluate(pack, tech, risk_output)
                        challenge_output = replace(challenge_output, evaluated_at=observed_at)
                        mech_path = os.path.join(scenario_temp, f"mechanism-{index}-{symbol}.json")
                        _write_json(mech_path, _filter_mechanism(legacy, observed_at, symbol))
                        comparison_record = comparator.compare(pack, tech, risk_output, challenge_output, mech_path)
                        comparison_record = replace(comparison_record, compared_at=observed_at)
                        synthesized = decision.synthesize(pack, tech, risk_output, challenge_output, comparison_record)
                        synthesized = replace(synthesized, evaluated_at=observed_at, model_version="decision-robustness-v1")
                        row = _decision_row(pack, tech, risk_output, challenge_output, comparison_record, synthesized, context, scenario)
                        decisions.append(row)
                        if context["news_sentiment"] is not None or context["venue_mode"] != "unmodeled":
                            if len(examples) < 12:
                                examples.append({"index": index, "symbol": symbol, "decision": row["decision"], "signal_direction": row["signal_direction"], "confidence": row["confidence"], "context": context})
                        for horizon in HORIZONS:
                            record = evaluate_decision(synthesized, risk_output, full_dir, horizon)
                            evaluation = to_dict(record)
                            evaluation.update({"evaluation_mode": "robustness_simulation", "robustness_batch_id": batch_id, "scenario": scenario, "lookahead_safe": True, "source_decision_id": row["decision_id"], "simulated_context": context})
                            outcome_dt = _parse_time(evaluation.get("outcome_timestamp")); decision_dt = _parse_time(evaluation.get("decision_timestamp"))
                            if outcome_dt and decision_dt and outcome_dt <= decision_dt:
                                leakage_violations.append(f"{scenario}:{row['decision_id']}:h{horizon}")
                            evaluations.append(evaluation)
                            row["horizons"][str(horizon)] = {"status": evaluation["status"], "evaluation": evaluation}
            summary, score = _scenario_summary(decisions, evaluations)
            scenario_summaries[scenario] = summary
            scenario_scores[scenario] = score
            scenario_examples[scenario] = examples
            all_decisions[scenario] = decisions

    baseline = scenario_summaries[SCENARIOS[0]]
    comparisons = {}
    baseline_rows = {row["decision_id"].split(":baseline_no_synthetic_context:")[-1]: row for row in all_decisions[SCENARIOS[0]]}
    for scenario, summary in scenario_summaries.items():
        scenario_rows = {row["decision_id"].split(f":{scenario}:")[-1]: row for row in all_decisions[scenario]}
        changed = sum(1 for key, row in scenario_rows.items() if key in baseline_rows and (row.get("decision"), row.get("signal_direction")) != (baseline_rows[key].get("decision"), baseline_rows[key].get("signal_direction")))
        comparisons[scenario] = {
            "accuracy_delta_vs_baseline_pct": None if summary["direction_accuracy_pct"] is None or baseline["direction_accuracy_pct"] is None else round(summary["direction_accuracy_pct"] - baseline["direction_accuracy_pct"], 2),
            "directional_count_delta_vs_baseline": summary["directional_records"] - baseline["directional_records"],
            "settled_count_delta_vs_baseline": summary["settled_records"] - baseline["settled_records"],
            "mean_confidence_delta_vs_baseline": None if summary["mean_decision_confidence"] is None or baseline["mean_decision_confidence"] is None else round(summary["mean_decision_confidence"] - baseline["mean_decision_confidence"], 2),
            "decision_state_changes_vs_baseline": changed,
            "decision_mix": summary["by_decision"],
        }
    report = {
        "robustness_batch_id": batch_id,
        "evaluation_mode": "robustness_simulation",
        "symbols": list(symbols),
        "timeframe": timeframe,
        "scenarios": list(SCENARIOS),
        "history_lengths": lengths,
        "common_bars": common_bars,
        "warmup_bars": MIN_WARMUP_BARS,
        "lookahead_safe": not leakage_violations,
        "leakage_checks": {"violation_count": len(leakage_violations), "violations": leakage_violations, "future_rows_used_for_decisions": False},
        "scenario_summaries": scenario_summaries,
        "comparisons_vs_baseline": comparisons,
        "decision_examples": scenario_examples,
        "limitations": [
            "Synthetic sentiment shifts are controlled perturbations, not reconstructed historical headlines or AI interpretations.",
            "Synthetic venue states test decision sensitivity to disagreement and feed failure; they are not historical exchange snapshots.",
            "Results are diagnostic and excluded from live calibration and promotion.",
            "ZECUSDT and ZAMAUSDT are excluded because their history is insufficient.",
        ],
    }
    report["determinism_hash"] = _output_hash({"summaries": scenario_summaries, "comparisons": comparisons})
    _write_json(os.path.join(output_dir, "robustness_report.json"), report)
    for scenario, score in scenario_scores.items():
        _write_json(os.path.join(output_dir, f"scorecard_{scenario}.json"), score)
    latest = os.path.join(root, "latest")
    _write_json(os.path.join(latest, "robustness_report.json"), report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run synthetic news and venue robustness simulation")
    parser.add_argument("--history-dir", default="history")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    report = run(args.history_dir, [x.strip() for x in args.symbols.split(",") if x.strip()], args.timeframe, args.output_dir)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
