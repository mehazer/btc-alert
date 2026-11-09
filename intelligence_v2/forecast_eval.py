"""Settle and score forecast records (walk-forward friendly, shadow-only)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .contracts import ForecastEvaluation, ForecastHorizonBand, ForecastRecord, ForecastScorecard, to_dict
from .forecast import HORIZONS

LEDGER_NAME = "forecast_ledger.json"
EVAL_NAME = "forecast_evaluation_ledger.json"
SCORECARD_NAME = "forecast_scorecard.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ledger_dir(history_dir: str) -> str:
    path = os.path.join(history_dir, "intelligence_v2")
    os.makedirs(path, exist_ok=True)
    return path


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
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


def _load_price_history(history_dir: str, symbol: str) -> List[dict]:
    for name in (f"{symbol}.json", f"{symbol}_4h.json"):
        path = os.path.join(history_dir, name)
        rows = _read_json(path, [])
        if isinstance(rows, list) and rows:
            return rows
    return []


def _future_prices(rows: List[dict], issued_at: str) -> Tuple[Optional[float], List[float]]:
    issued = _parse_time(issued_at)
    if issued is None:
        return None, []
    prior = None
    future: List[float] = []
    for row in rows:
        ts = _parse_time(row.get("date") or row.get("timestamp"))
        price = row.get("price")
        if ts is None or price is None:
            continue
        if ts <= issued:
            prior = float(price)
        else:
            future.append(float(price))
    return prior, future


def append_forecasts(history_dir: str, forecasts: List[ForecastRecord]) -> dict:
    path = os.path.join(_ledger_dir(history_dir), LEDGER_NAME)
    existing = _read_json(path, [])
    if not isinstance(existing, list):
        existing = []
    seen = {r.get("forecast_id") for r in existing if isinstance(r, dict)}
    added = 0
    for fc in forecasts:
        if fc.forecast_id in seen:
            continue
        existing.append(to_dict(fc))
        added += 1
    _write_json(path, existing)
    return {"path": path, "added": added, "total": len(existing)}


def evaluate_forecast(fc: ForecastRecord, history_dir: str, horizon_bars: int) -> ForecastEvaluation:
    band = next((h for h in fc.horizons if h.horizon_bars == horizon_bars), None)
    if fc.stance == "ABSTAIN" or (band and band.status == "abstain"):
        return ForecastEvaluation(
            agent="forecast-evaluator",
            forecast_id=fc.forecast_id,
            run_id=fc.run_id,
            symbol=fc.symbol,
            timeframe=fc.timeframe,
            horizon_bars=horizon_bars,
            status="abstained",
            issued_at=fc.issued_at,
            direction="abstain",
            probability_low=band.probability_low if band else 0.45,
            probability_high=band.probability_high if band else 0.55,
            forward_return_pct=None,
            direction_correct=None,
            inside_move_band=None,
            invalidated=None,
            notes=["Forecast abstained — not scored for direction accuracy."],
        )

    direction = band.direction if band else fc.primary_direction
    p_lo = band.probability_low if band else 0.5
    p_hi = band.probability_high if band else 0.5
    rows = _load_price_history(history_dir, fc.symbol)
    anchor, future = _future_prices(rows, fc.issued_at)
    notes: List[str] = []
    if anchor is None:
        return ForecastEvaluation(
            agent="forecast-evaluator",
            forecast_id=fc.forecast_id,
            run_id=fc.run_id,
            symbol=fc.symbol,
            timeframe=fc.timeframe,
            horizon_bars=horizon_bars,
            status="insufficient_data",
            issued_at=fc.issued_at,
            direction=direction,
            probability_low=p_lo,
            probability_high=p_hi,
            forward_return_pct=None,
            direction_correct=None,
            inside_move_band=None,
            invalidated=None,
            notes=["No anchor price at issue time."],
        )
    if len(future) < horizon_bars:
        notes.append(f"Only {len(future)}/{horizon_bars} future bars available.")
        return ForecastEvaluation(
            agent="forecast-evaluator",
            forecast_id=fc.forecast_id,
            run_id=fc.run_id,
            symbol=fc.symbol,
            timeframe=fc.timeframe,
            horizon_bars=horizon_bars,
            status="pending",
            issued_at=fc.issued_at,
            direction=direction,
            probability_low=p_lo,
            probability_high=p_hi,
            forward_return_pct=None,
            direction_correct=None,
            inside_move_band=None,
            invalidated=None,
            notes=notes,
        )

    end_price = future[horizon_bars - 1]
    fwd = (end_price - anchor) / anchor * 100.0
    if direction == "up":
        correct = fwd > 0
    elif direction == "down":
        correct = fwd < 0
    else:
        correct = abs(fwd) < 0.3

    inside = None
    if band and band.expected_move_pct_low is not None and band.expected_move_pct_high is not None:
        lo, hi = band.expected_move_pct_low, band.expected_move_pct_high
        inside = lo <= fwd <= hi

    return ForecastEvaluation(
        agent="forecast-evaluator",
        forecast_id=fc.forecast_id,
        run_id=fc.run_id,
        symbol=fc.symbol,
        timeframe=fc.timeframe,
        horizon_bars=horizon_bars,
        status="settled",
        issued_at=fc.issued_at,
        direction=direction,
        probability_low=p_lo,
        probability_high=p_hi,
        forward_return_pct=round(fwd, 4),
        direction_correct=correct,
        inside_move_band=inside,
        invalidated=False,
        notes=notes,
    )


def _rebuild_forecast(raw: dict) -> ForecastRecord:
    bands = []
    for h in raw.get("horizons") or []:
        bands.append(
            ForecastHorizonBand(
                horizon_bars=int(h.get("horizon_bars") or 0),
                direction=h.get("direction") or "abstain",
                probability_low=float(h.get("probability_low") or 0.5),
                probability_high=float(h.get("probability_high") or 0.5),
                expected_move_pct_low=h.get("expected_move_pct_low"),
                expected_move_pct_high=h.get("expected_move_pct_high"),
                status=h.get("status") or "abstain",
            )
        )
    return ForecastRecord(
        agent=raw.get("agent") or "forecast-engine",
        run_id=raw.get("run_id") or "",
        symbol=raw.get("symbol") or "",
        timeframe=raw.get("timeframe") or "4h",
        forecast_id=raw.get("forecast_id") or "",
        issued_at=raw.get("issued_at") or "",
        stance=raw.get("stance") or "ABSTAIN",
        primary_direction=raw.get("primary_direction") or "abstain",
        horizons=bands,
        confidence=int(raw.get("confidence") or 0),
        probability_band=raw.get("probability_band") or "abstain",
        drivers=raw.get("drivers") or [],
        invalidation=raw.get("invalidation") or [],
        abstain_reasons=raw.get("abstain_reasons") or [],
        evidence_ids=raw.get("evidence_ids") or [],
        model_version=raw.get("model_version") or "",
        calibration_notes=raw.get("calibration_notes") or [],
        execution_allowed=False,
    )


def settle_forecasts(history_dir: str) -> dict:
    ledger_path = os.path.join(_ledger_dir(history_dir), LEDGER_NAME)
    eval_path = os.path.join(_ledger_dir(history_dir), EVAL_NAME)
    forecasts = _read_json(ledger_path, [])
    evaluations = _read_json(eval_path, [])
    if not isinstance(forecasts, list):
        forecasts = []
    if not isinstance(evaluations, list):
        evaluations = []

    existing_keys = {
        (e.get("forecast_id"), e.get("horizon_bars")): i
        for i, e in enumerate(evaluations)
        if isinstance(e, dict)
    }

    new_settled = 0
    for raw in forecasts:
        if not isinstance(raw, dict):
            continue
        fc = _rebuild_forecast(raw)
        for h in HORIZONS:
            key = (fc.forecast_id, h)
            prev = evaluations[existing_keys[key]] if key in existing_keys else None
            if prev and prev.get("status") in {"settled", "abstained"}:
                continue
            ev = evaluate_forecast(fc, history_dir, h)
            payload = to_dict(ev)
            if key in existing_keys:
                evaluations[existing_keys[key]] = payload
            else:
                existing_keys[key] = len(evaluations)
                evaluations.append(payload)
            if ev.status == "settled":
                new_settled += 1

    _write_json(eval_path, evaluations)
    scorecard = build_forecast_scorecard(forecasts, evaluations)
    _write_json(os.path.join(_ledger_dir(history_dir), SCORECARD_NAME), to_dict(scorecard))
    return {
        "evaluations": len(evaluations),
        "newly_settled": new_settled,
        "scorecard": to_dict(scorecard),
    }


def build_forecast_scorecard(forecasts: List[dict], evaluations: List[dict]) -> ForecastScorecard:
    abstain = sum(1 for f in forecasts if (f.get("stance") == "ABSTAIN"))
    active = sum(1 for f in forecasts if (f.get("stance") == "FORECAST"))
    settled = [e for e in evaluations if e.get("status") == "settled"]
    pending = [e for e in evaluations if e.get("status") == "pending"]
    directional = [
        e for e in settled
        if e.get("direction") in {"up", "down"} and e.get("direction_correct") is not None
    ]
    hits = [e for e in directional if e.get("direction_correct") is True]

    by_horizon: Dict[str, Dict[str, Any]] = {}
    for h in HORIZONS:
        rows = [e for e in directional if int(e.get("horizon_bars") or 0) == h]
        h_hits = [e for e in rows if e.get("direction_correct")]
        by_horizon[str(h)] = {
            "directional": len(rows),
            "hits": len(h_hits),
            "accuracy_pct": round(len(h_hits) / len(rows) * 100, 2) if rows else None,
            "pending": sum(1 for e in pending if int(e.get("horizon_bars") or 0) == h),
        }

    by_band: Dict[str, Dict[str, Any]] = {}
    band_by_id = {f.get("forecast_id"): f.get("probability_band") for f in forecasts}
    for e in directional:
        band = band_by_id.get(e.get("forecast_id")) or "unknown"
        slot = by_band.setdefault(band, {"directional": 0, "hits": 0})
        slot["directional"] += 1
        if e.get("direction_correct"):
            slot["hits"] += 1
    for band, slot in by_band.items():
        n = slot["directional"]
        slot["accuracy_pct"] = round(slot["hits"] / n * 100, 2) if n else None

    calibration = []
    mid = {"low": 0.55, "medium": 0.62, "high": 0.70, "abstain": 0.50}
    for band, slot in sorted(by_band.items()):
        calibration.append({
            "probability_band": band,
            "implied_mid": mid.get(band),
            "empirical_hit_rate": (slot["accuracy_pct"] / 100.0) if slot.get("accuracy_pct") is not None else None,
            "n": slot["directional"],
            "note": "Needs larger n before adjusting bands.",
        })

    return ForecastScorecard(
        agent="forecast-scorecard",
        evaluated_at=_utc_now(),
        forecasts_total=len(forecasts),
        active_forecasts=active,
        abstain_count=abstain,
        abstain_rate_pct=round(abstain / len(forecasts) * 100, 2) if forecasts else None,
        settled_rows=len(settled),
        pending_rows=len(pending),
        directional_settled=len(directional),
        directional_hits=len(hits),
        direction_accuracy_pct=round(len(hits) / len(directional) * 100, 2) if directional else None,
        by_horizon=by_horizon,
        by_probability_band=by_band,
        calibration=calibration,
        limitations=[
            "Shadow-only: forecasts never execute or alter production alerts.",
            "Direction accuracy excludes ABSTAIN rows (abstain is tracked separately).",
            "Probability bands are conservative priors pending walk-forward calibration.",
            "Do not enable self-learning until calibration is stable out of sample.",
        ],
    )


def process_forecasts(history_dir: str, forecasts: List[ForecastRecord]) -> dict:
    append_info = append_forecasts(history_dir, forecasts)
    settle_info = settle_forecasts(history_dir)
    return {"append": append_info, "settle": settle_info}
