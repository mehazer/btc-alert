"""Historical outcome evaluator for v2 paper decisions.

The evaluator is point-in-time safe: it only uses rows after the decision
observation. If a horizon has not elapsed, it returns PENDING rather than
counting the decision as a failure.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from .contracts import DecisionRecord, EvaluationRecord, RiskAssessment, Scorecard


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _load_history(history_dir: str, symbol: str) -> List[dict]:
    try:
        with open(os.path.join(history_dir, f"{symbol}.json"), "r", encoding="utf-8") as handle:
            rows = json.load(handle)
        return rows if isinstance(rows, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _future_rows(rows: List[dict], decision_timestamp: str) -> Tuple[Optional[dict], List[dict]]:
    decision_dt = _parse_time(decision_timestamp)
    if decision_dt is None:
        return None, []
    candidates = []
    for index, row in enumerate(rows):
        observed = _parse_time(str(row.get("date") or row.get("timestamp") or ""))
        if observed and observed <= decision_dt:
            candidates.append((index, row, observed))
    if not candidates:
        return None, []
    index, decision_row, _ = candidates[-1]
    return decision_row, rows[index + 1:]


def _direction_from_return(value: Optional[float]) -> Optional[bool]:
    if value is None or value == 0:
        return None
    return value > 0


def evaluate_decision(
    decision: DecisionRecord,
    risk: RiskAssessment,
    history_dir: str,
    horizon_bars: int,
) -> EvaluationRecord:
    rows = _load_history(history_dir, decision.symbol)
    decision_row, future = _future_rows(rows, decision.evaluated_at)
    guarded = risk.veto or risk.overall_level in {"high", "medium", "unknown"} or decision.gate == "blocked"
    notes: List[str] = []
    if decision.signal_direction == "up":
        expected_up = True
    elif decision.signal_direction == "down":
        expected_up = False
    else:
        expected_up = None
    if decision_row is None or decision_row.get("price") is None:
        return EvaluationRecord(
            agent="evaluator", run_id=decision.run_id, symbol=decision.symbol, timeframe=decision.timeframe,
            decision=decision.decision, signal_direction=decision.signal_direction, horizon_bars=horizon_bars,
            status="insufficient_data", decision_timestamp=decision.evaluated_at,
            outcome_timestamp=None, forward_return_pct=None, max_favorable_excursion_pct=None,
            max_adverse_excursion_pct=None,
            direction_correct=None, guarded=guarded, risk_warning_supported=None, confidence=decision.confidence,
            notes=["No matching decision-time price was found."],
        )
    usable = [row for row in future if row.get("price") is not None][:horizon_bars]
    if len(usable) < horizon_bars:
        notes.append(f"Only {len(usable)}/{horizon_bars} future bars are available.")
        return EvaluationRecord(
            agent="evaluator", run_id=decision.run_id, symbol=decision.symbol, timeframe=decision.timeframe,
            decision=decision.decision, signal_direction=decision.signal_direction, horizon_bars=horizon_bars,
            status="pending", decision_timestamp=decision.evaluated_at,
            outcome_timestamp=str(usable[-1].get("date")) if usable else None,
            forward_return_pct=None, max_favorable_excursion_pct=None, max_adverse_excursion_pct=None,
            direction_correct=None, guarded=guarded, risk_warning_supported=None, confidence=decision.confidence,
            notes=notes,
        )
    entry = float(decision_row["price"])
    returns = [((float(row["price"]) - entry) / entry) * 100 for row in usable]
    forward_return = returns[-1]
    mfe = max(returns)
    mae = min(returns)
    direction_correct = None if expected_up is None else ((_direction_from_return(forward_return) == expected_up) if forward_return != 0 else False)
    risk_supported = None
    if guarded:
        risk_supported = mae <= -1.0
        notes.append("Guarded decision was supported by a >=1% adverse excursion." if risk_supported else "Guarded decision did not reach the 1% adverse-excursion support threshold.")
    return EvaluationRecord(
        agent="evaluator", run_id=decision.run_id, symbol=decision.symbol, timeframe=decision.timeframe,
        decision=decision.decision, signal_direction=decision.signal_direction, horizon_bars=horizon_bars,
        status="settled", decision_timestamp=decision.evaluated_at,
        outcome_timestamp=str(usable[-1].get("date")), forward_return_pct=round(forward_return, 6),
        max_favorable_excursion_pct=round(mfe, 6), max_adverse_excursion_pct=round(mae, 6),
        direction_correct=direction_correct, guarded=guarded, risk_warning_supported=risk_supported,
        confidence=decision.confidence, notes=notes,
    )


def build_scorecard(
    decisions: Iterable[DecisionRecord],
    risks: Iterable[RiskAssessment],
    history_dir: str,
    horizons: Tuple[int, ...] = (1, 3, 6),
) -> Scorecard:
    decision_list = list(decisions)
    records: List[EvaluationRecord] = []
    risks_by_symbol = {risk.symbol: risk for risk in risks}
    for decision in decision_list:
        risk = risks_by_symbol.get(decision.symbol)
        if risk is None:
            continue
        for horizon in horizons:
            records.append(evaluate_decision(decision, risk, history_dir, horizon))
    settled = [record for record in records if record.status == "settled"]
    directional = [record for record in settled if record.direction_correct is not None]
    hits = [record for record in directional if record.direction_correct]
    guarded = [record for record in settled if record.guarded and record.risk_warning_supported is not None]
    supported = [record for record in guarded if record.risk_warning_supported]
    by_decision: Dict[str, Dict[str, int]] = {}
    for record in records:
        by_decision.setdefault(record.decision, {"records": 0, "settled": 0, "pending": 0})
        by_decision[record.decision]["records"] += 1
        if record.status == "settled":
            by_decision[record.decision]["settled"] += 1
        elif record.status == "pending":
            by_decision[record.decision]["pending"] += 1
    run_id = decision_list[0].run_id if decision_list else "no-decisions"
    return Scorecard(
        agent="evaluator",
        run_id=run_id,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        horizons=list(horizons),
        decisions_total=len(decision_list),
        records_total=len(records),
        settled_records=len(settled),
        pending_records=sum(1 for r in records if r.status == "pending"),
        directional_records=len(directional),
        directional_hits=len(hits),
        direction_accuracy_pct=round(len(hits) / len(directional) * 100, 2) if directional else None,
        guarded_records=len(guarded),
        supported_risk_warnings=len(supported),
        risk_warning_support_pct=round(len(supported) / len(guarded) * 100, 2) if guarded else None,
        by_decision=by_decision,
        records=records,
        limitations=[
            "Point-in-time evaluation only; pending horizons are excluded from accuracy.",
            "Risk warning support requires medium/high/unknown/veto and >=1% adverse excursion.",
        ],
    )
