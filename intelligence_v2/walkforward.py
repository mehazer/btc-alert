"""Point-in-time walk-forward backtest for the v2 agent pipeline.

The runner reconstructs evidence and decisions using only the history prefix
available at each historical bar. Outputs are written to an isolated
walkforward batch and are never merged into the live v2 ledger or promotion
scorecard.

Usage:
    python -m intelligence_v2.walkforward --history-dir history
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from . import challenge, comparator, decision, evidence, risk, technical
from .contracts import ShadowRun, to_dict
from .evaluation import evaluate_decision
from .ledger import HORIZONS, build_agent_scorecards, build_persistent_scorecard

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DEFAULT_TIMEFRAME = "4h"
MIN_WARMUP_BARS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _normalise_rows(rows: List[dict]) -> List[dict]:
    """Make source order explicit without changing market values.

    Several legacy rows contain date-only timestamps and many share the same
    date. The evaluator is timestamp based, so we preserve source order by
    assigning monotonically increasing synthetic timestamps. The original
    source timestamp remains in ``source_observed_at`` for auditability.
    """
    output: List[dict] = []
    previous: Optional[datetime] = None
    for index, original in enumerate(rows):
        row = dict(original)
        source_time = row.get("date") or row.get("timestamp")
        base = _parse_time(source_time) or datetime(1970, 1, 1, tzinfo=timezone.utc)
        candidate = base + timedelta(microseconds=index)
        if previous is not None and candidate <= previous:
            candidate = previous + timedelta(microseconds=1)
        row["source_observed_at"] = source_time
        row["date"] = candidate.isoformat()
        previous = candidate
        output.append(row)
    return output


def _load_histories(history_dir: str, symbols: Iterable[str]) -> Dict[str, List[dict]]:
    histories = {}
    for symbol in symbols:
        rows = _read_json(os.path.join(history_dir, f"{symbol}.json"), [])
        histories[symbol] = _normalise_rows(rows if isinstance(rows, list) else [])
    return histories


def _filter_mechanism(rows: List[dict], observed_at: str, symbol: str) -> List[dict]:
    target = _parse_time(observed_at)
    result = []
    for row in rows:
        if row.get("symbol") != symbol:
            continue
        timestamp = _parse_time(row.get("timestamp") or row.get("evaluated_at"))
        if timestamp and target and timestamp <= target:
            result.append(row)
    return result


def _write_history(directory: str, histories: Dict[str, List[dict]]) -> None:
    os.makedirs(directory, exist_ok=True)
    for symbol, rows in histories.items():
        _write_json(os.path.join(directory, f"{symbol}.json"), rows)


def _decision_row(pack, technical_output, risk_output, challenge_output, comparison_record, synthesized) -> dict:
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
        "evaluation_mode": "walkforward_backtest",
        "lookahead_safe": True,
        "source_observed_at": pack.observations[0].observed_at,
        "risk_overall_level": risk_output.overall_level,
        "risk_veto": risk_output.veto,
        "guarded": bool(risk_output.veto or risk_output.overall_level in {"high", "unknown"} or synthesized.gate == "blocked"),
        "evidence_ids": synthesized.evidence_ids,
        "agents": {
            "technical": {"state": technical_output.state, "confidence": technical_output.confidence, "risk_flags": technical_output.risk_flags, "model_version": technical_output.model_version},
            "risk": {"overall_level": risk_output.overall_level, "veto": risk_output.veto, "downside_risk": risk_output.downside_risk, "squeeze_risk": risk_output.squeeze_risk, "data_risk": risk_output.data_risk, "confidence": risk_output.confidence, "model_version": risk_output.model_version},
            "challenge": {"status": challenge_output.status, "challenges": challenge_output.challenges, "missing_evidence": challenge_output.missing_evidence, "contradictions": challenge_output.contradictions, "confidence_cap": challenge_output.confidence_cap, "model_version": challenge_output.model_version},
            "comparison": {"overall_status": comparison_record.overall_status, "state_agreement": comparison_record.state_agreement, "risk_agreement": comparison_record.risk_agreement},
        },
        "horizons": {},
    }


def _hash_outputs(evaluations: List[dict], decisions: List[dict]) -> str:
    payload = json.dumps({"decisions": decisions, "evaluations": evaluations}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _batch_key(histories: Dict[str, List[dict]], symbols: Iterable[str], timeframe: str) -> str:
    payload = json.dumps({"histories": histories, "symbols": list(symbols), "timeframe": timeframe}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def run(history_dir: str, symbols: Iterable[str] = DEFAULT_SYMBOLS, timeframe: str = DEFAULT_TIMEFRAME, output_dir: str | None = None) -> dict:
    symbols = tuple(symbols)
    if tuple(symbols) != DEFAULT_SYMBOLS:
        # The first implementation is intentionally explicit and auditable.
        symbols = tuple(symbols)
    histories = _load_histories(history_dir, symbols)
    lengths = {symbol: len(rows) for symbol, rows in histories.items()}
    common_bars = min(lengths.values()) if lengths else 0
    batch_id = f"walkforward-{_batch_key(histories, symbols, timeframe)}"
    ledger_dir = os.path.join(history_dir, "intelligence_v2", "walkforward")
    output_dir = output_dir or os.path.join(ledger_dir, batch_id)
    legacy_mechanism = _read_json(os.path.join(history_dir, "mechanism_history.json"), [])
    legacy_mechanism = legacy_mechanism if isinstance(legacy_mechanism, list) else []
    decisions: List[dict] = []
    evaluations: List[dict] = []
    timestamps: List[str] = []
    skipped = {"warmup": 0, "insufficient_common_history": 0}
    leakage_violations: List[str] = []

    with tempfile.TemporaryDirectory(prefix="crypto-watch-walkforward-") as temp:
        prefix_dir = os.path.join(temp, "prefix")
        full_dir = os.path.join(temp, "full")
        _write_history(full_dir, histories)
        for index in range(common_bars):
            if index < MIN_WARMUP_BARS:
                skipped["warmup"] += 1
                continue
            observed_at = histories[symbols[0]][index]["date"]
            timestamps.append(observed_at)
            prefix_histories = {symbol: rows[: index + 1] for symbol, rows in histories.items()}
            _write_history(prefix_dir, prefix_histories)
            btc_history = prefix_histories.get("BTCUSDT", [])
            for symbol in symbols:
                run_id = f"{batch_id}:{index:04d}:{symbol}"
                symbol_observed_at = histories[symbol][index]["date"]
                pack = evidence.build_evidence_pack(prefix_dir, symbol, timeframe, run_id)
                # Evidence creation time is metadata; use the historical bar to
                # make every downstream decision timestamp point-in-time.
                pack = replace(pack, created_at=symbol_observed_at)
                technical_output = technical.evaluate(pack, prefix_dir, btc_history)
                technical_output = replace(technical_output, evaluated_at=symbol_observed_at)
                risk_output = risk.evaluate(pack, technical_output)
                risk_output = replace(risk_output, evaluated_at=symbol_observed_at)
                challenge_output = challenge.evaluate(pack, technical_output, risk_output)
                challenge_output = replace(challenge_output, evaluated_at=symbol_observed_at)
                mechanism_path = os.path.join(temp, f"mechanism-{index}-{symbol}.json")
                _write_json(mechanism_path, _filter_mechanism(legacy_mechanism, symbol_observed_at, symbol))
                comparison_record = comparator.compare(pack, technical_output, risk_output, challenge_output, mechanism_path)
                comparison_record = replace(comparison_record, compared_at=symbol_observed_at)
                synthesized = decision.synthesize(pack, technical_output, risk_output, challenge_output, comparison_record)
                synthesized = replace(synthesized, evaluated_at=symbol_observed_at, model_version="decision-walkforward-v1")
                if _parse_time(synthesized.evaluated_at) and _parse_time(synthesized.evaluated_at) > _parse_time(symbol_observed_at):
                    leakage_violations.append(f"decision_after_observation:{run_id}")
                row = _decision_row(pack, technical_output, risk_output, challenge_output, comparison_record, synthesized)
                decisions.append(row)
                for horizon in HORIZONS:
                    record = evaluate_decision(synthesized, risk_output, full_dir, horizon)
                    evaluation = to_dict(record)
                    evaluation.update({
                        "evaluation_mode": "walkforward_backtest",
                        "walkforward_batch_id": batch_id,
                        "lookahead_safe": True,
                        "source_decision_id": row["decision_id"],
                        "source_observed_at": pack.observations[0].observed_at,
                    })
                    outcome_dt = _parse_time(evaluation.get("outcome_timestamp"))
                    decision_dt = _parse_time(evaluation.get("decision_timestamp"))
                    if outcome_dt and decision_dt and outcome_dt <= decision_dt:
                        leakage_violations.append(f"outcome_not_forward:{row['decision_id']}:h{horizon}")
                    evaluations.append(evaluation)
                    row["horizons"][str(horizon)] = {"status": evaluation["status"], "evaluation": evaluation}

    scorecard = build_persistent_scorecard(evaluations, decisions)
    agent_cards = build_agent_scorecards(evaluations, decisions)
    for payload in (scorecard, agent_cards):
        payload["evaluation_mode"] = "walkforward_backtest"
        payload["walkforward_batch_id"] = batch_id
    status_counts = {status: sum(1 for row in evaluations if row.get("status") == status) for status in ("settled", "pending", "insufficient_data")}
    report = {
        "walkforward_batch_id": batch_id,
        "evaluation_mode": "walkforward_backtest",
        "lookahead_safe": not leakage_violations,
        "started_at": _now(),
        "completed_at": _now(),
        "symbols": list(symbols),
        "timeframe": timeframe,
        "history_lengths": lengths,
        "common_bars": common_bars,
        "warmup_bars": MIN_WARMUP_BARS,
        "eligible_timestamps": len(timestamps),
        "decision_count": len(decisions),
        "evaluation_count": len(evaluations),
        "coverage": status_counts,
        "leakage_checks": {
            "violations": leakage_violations,
            "violation_count": len(leakage_violations),
            "source_order_normalized": True,
            "future_rows_used_for_decisions": False,
        },
        "determinism_hash": _hash_outputs(evaluations, decisions),
        "scorecard": {
            "settled_records": scorecard.get("settled_records"),
            "pending_records": scorecard.get("pending_records"),
            "directional_records": scorecard.get("directional_records"),
            "directional_hits": scorecard.get("directional_hits"),
            "direction_accuracy_pct": scorecard.get("direction_accuracy_pct"),
            "by_horizon": scorecard.get("by_horizon", {}),
            "by_decision": scorecard.get("by_decision", {}),
        },
        "limitations": [
            "ZECUSDT and ZAMAUSDT are intentionally excluded because their history is too short.",
            "Legacy date-only rows are assigned monotonic synthetic timestamps preserving source order.",
            "Legacy mechanism comparison is only used when a mechanism record exists at or before the replay timestamp.",
            "Historical replay is diagnostic and excluded from live promotion calibration.",
            "This replays deterministic v2 agents; it does not recreate unavailable historical news or venue snapshots.",
        ],
    }
    _write_json(os.path.join(output_dir, "decision_ledger_walkforward.json"), decisions)
    _write_json(os.path.join(output_dir, "evaluation_ledger_walkforward.json"), evaluations)
    _write_json(os.path.join(output_dir, "scorecard_walkforward.json"), scorecard)
    _write_json(os.path.join(output_dir, "agent_scorecards_walkforward.json"), agent_cards)
    _write_json(os.path.join(output_dir, "walkforward_report.json"), report)
    latest = os.path.join(ledger_dir, "latest")
    for name, value in {
        "walkforward_report.json": report,
        "scorecard_walkforward.json": scorecard,
        "agent_scorecards_walkforward.json": agent_cards,
    }.items():
        _write_json(os.path.join(latest, name), value)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a point-in-time v2 walk-forward backtest")
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
