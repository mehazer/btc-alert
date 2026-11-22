"""Experimental trend/volume candidate, isolated from production decisions."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

PREDICTION_LEDGER = "trend_volume_candidate_ledger.json"
EVALUATION_LEDGER = "trend_volume_candidate_evaluations.json"
MODEL_VERSION = "trend-volume-shadow-v1"
VOLUME_RATIO_MIN = 1.35
TREND_LOOKBACK_BARS = 6
VOLUME_LOOKBACK_BARS = 20


def _read(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def _direction_from_state(state: str) -> Optional[str]:
    state = (state or "").lower()
    if state in {"fresh-long-buildup", "short-covering", "bullish", "overheated", "spot-led-buying", "cautious-long-bias"}:
        return "up"
    if state in {"weakening-participation", "bearish", "fear", "fresh-short-buildup", "long-liquidation", "soft-selling-drift", "spot-led-selling"}:
        return "down"
    return None


def _closed_rows(history_dir: str, symbol: str) -> List[dict]:
    path = os.path.join(history_dir, f"{symbol}.json")
    rows = _read(path, [])
    if not isinstance(rows, list):
        return []
    # New candidate is fail-closed: legacy records without explicit completion
    # metadata are not assumed to be closed observations.
    closed = [r for r in rows if isinstance(r, dict) and r.get("bar_complete") is True and r.get("bar_close_ts") is not None]
    closed.sort(key=lambda r: int(r["bar_close_ts"]))
    deduped = {}
    for row in closed:
        deduped[int(row["bar_close_ts"])] = row
    return list(deduped.values())


def make_candidate(history_dir: str, symbol: str, technical_state: str) -> dict:
    rows = _closed_rows(history_dir, symbol)
    issued_at = datetime.now(timezone.utc).isoformat()
    close_ts = rows[-1].get("bar_close_ts") if rows else None
    bar_id = f"{symbol}|{close_ts}|{MODEL_VERSION}"
    candidate_id = "tv-" + hashlib.sha1(bar_id.encode("utf-8")).hexdigest()[:12]
    reasons: List[str] = []
    direction = "abstain"
    snapshot: Dict[str, Any] = {}

    if len(rows) < max(TREND_LOOKBACK_BARS + 1, VOLUME_LOOKBACK_BARS + 1):
        reasons.append("insufficient completed-bar history")
    else:
        current = rows[-1]
        try:
            current_price = float(current["price"])
            trend_price = float(rows[-1 - TREND_LOOKBACK_BARS]["price"])
            volumes = [float(row["volume"]) for row in rows[-(VOLUME_LOOKBACK_BARS + 1):-1] if row.get("volume") is not None]
            current_volume = float(current["volume"])
            volume_median = median(volumes) if len(volumes) == VOLUME_LOOKBACK_BARS else None
        except (KeyError, TypeError, ValueError):
            current_price = trend_price = current_volume = 0.0
            volume_median = None
        trend_return_pct = (current_price / trend_price - 1) * 100 if trend_price else None
        volume_ratio = current_volume / volume_median if volume_median else None
        technical_direction = _direction_from_state(technical_state)
        trend_direction = "up" if trend_return_pct is not None and trend_return_pct > 0 else "down" if trend_return_pct is not None and trend_return_pct < 0 else None
        try:
            previous_price = float(rows[-2]["price"])
        except (IndexError, KeyError, TypeError, ValueError):
            previous_price = None
        persistence_direction = "up" if previous_price is not None and current_price > previous_price else "down" if previous_price is not None and current_price < previous_price else "unknown"
        snapshot = {
            "bar_close_ts": close_ts,
            "bar_open_ts": current.get("bar_open_ts"),
            "captured_at_ms": current.get("captured_at_ms"),
            "price": current_price,
            "volume": current_volume,
            "trend_6bar_return_pct": round(trend_return_pct, 6) if trend_return_pct is not None else None,
            "relative_volume_median_20": round(volume_ratio, 6) if volume_ratio is not None else None,
            "technical_state": technical_state,
            "technical_direction": technical_direction,
            "trend_direction": trend_direction,
            "persistence_direction": persistence_direction,
            "oi": current.get("open_interest", current.get("oi")),
            "funding_rate": current.get("funding_rate", current.get("funding")),
        }
        if technical_direction is None:
            reasons.append("technical direction unavailable")
        elif trend_direction != technical_direction:
            reasons.append("technical direction does not agree with 6-bar trend")
        if volume_ratio is None or volume_ratio < VOLUME_RATIO_MIN:
            reasons.append(f"relative volume below {VOLUME_RATIO_MIN:.2f} threshold or unavailable")
        if not reasons:
            direction = technical_direction
            reasons.append("technical direction agrees with trend and closed-bar relative volume confirms")

    return {
        "candidate_id": candidate_id,
        "model_version": MODEL_VERSION,
        "symbol": symbol,
        "timeframe": "4h",
        "issued_at": issued_at,
        "bar_close_ts": close_ts,
        "direction": direction,
        "status": "pending",
        "reasons": reasons,
        "feature_snapshot": snapshot,
        "execution_allowed": False,
    }


def _forward_close(rows: List[dict], close_ts: int) -> Optional[float]:
    for row in rows:
        try:
            if row.get("bar_complete") is True and int(row.get("bar_close_ts")) > int(close_ts):
                return float(row["price"])
        except (TypeError, ValueError, KeyError):
            continue
    return None


def process_candidates(history_dir: str, candidates: List[dict], persist: bool = True) -> dict:
    directory = os.path.join(history_dir, "intelligence_v2")
    predictions_path = os.path.join(directory, PREDICTION_LEDGER)
    evaluations_path = os.path.join(directory, EVALUATION_LEDGER)
    predictions = _read(predictions_path, [])
    evaluations = _read(evaluations_path, [])
    if not isinstance(predictions, list):
        predictions = []
    if not isinstance(evaluations, list):
        evaluations = []
    seen = {row.get("candidate_id") for row in predictions if isinstance(row, dict)}
    added = 0
    if persist:
        for candidate in candidates:
            if candidate.get("bar_close_ts") is None:
                continue
            if candidate.get("candidate_id") not in seen:
                predictions.append(candidate)
                seen.add(candidate.get("candidate_id"))
                added += 1

        evaluated_ids = {row.get("candidate_id") for row in evaluations if isinstance(row, dict)}
        for candidate in predictions:
            candidate_id = candidate.get("candidate_id")
            close_ts = candidate.get("bar_close_ts")
            if candidate_id in evaluated_ids or close_ts is None:
                continue
            rows = _closed_rows(history_dir, candidate.get("symbol", ""))
            anchor = candidate.get("feature_snapshot", {}).get("price")
            future_close = _forward_close(rows, int(close_ts))
            if anchor in (None, 0) or future_close is None:
                continue
            forward_return = (future_close / float(anchor) - 1) * 100
            direction = candidate.get("direction")
            correct = (direction == "up" and forward_return > 0) or (direction == "down" and forward_return < 0) if direction in {"up", "down"} else None
            evaluations.append({
                "candidate_id": candidate_id,
                "model_version": candidate.get("model_version"),
                "symbol": candidate.get("symbol"),
                "horizon_bars": 1,
                "status": "settled",
                "direction": direction,
                "forward_return_pct": round(forward_return, 6),
                "direction_correct": correct,
                "baseline_directions": {"persistence": candidate.get("feature_snapshot", {}).get("persistence_direction")},
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            })
            evaluated_ids.add(candidate_id)

        _write(predictions_path, predictions)
        _write(evaluations_path, evaluations)

    outcomes = [row for row in evaluations if row.get("status") == "settled" and row.get("forward_return_pct") is not None]
    scored = [row for row in outcomes if row.get("direction") in {"up", "down"}]
    hits = sum(1 for row in scored if row.get("direction_correct") is True)
    fired = sum(1 for row in predictions if row.get("direction") in {"up", "down"})
    baselines = {}
    for name, fixed_direction in (("always_up", "up"), ("always_down", "down"), ("persistence", None)):
        eligible = baseline_hits = 0
        for row in outcomes:
            direction = fixed_direction or row.get("baseline_directions", {}).get(name)
            ret = float(row["forward_return_pct"])
            if direction not in {"up", "down"}:
                continue
            eligible += 1
            baseline_hits += int((direction == "up" and ret > 0) or (direction == "down" and ret < 0))
        baselines[name] = {"n": eligible, "hits": baseline_hits, "accuracy_pct": round(100 * baseline_hits / eligible, 2) if eligible else None}
    evaluated_ids = {row.get("candidate_id") for row in evaluations}
    pending = sum(1 for row in predictions if row.get("bar_close_ts") is not None and row.get("candidate_id") not in evaluated_ids)
    return {
        "model_version": MODEL_VERSION,
        "new_predictions": added,
        "predictions_total": len(predictions),
        "settled_directional": len(scored),
        "hits": hits,
        "direction_accuracy_pct": round(100 * hits / len(scored), 2) if scored else None,
        "fired_count": fired,
        "abstain_count": sum(1 for row in predictions if row.get("direction") == "abstain"),
        "coverage_pct": round(100 * fired / len(predictions), 2) if predictions else None,
        "outcome_n_including_abstentions": len(outcomes),
        "baselines": baselines,
        "pending": pending,
        "execution_allowed": False,
        "note": "Shadow-only candidate metric; small samples and overlapping symbols are not independent. No cost adjustment is applied.",
    }
