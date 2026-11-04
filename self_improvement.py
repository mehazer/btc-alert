"""Deterministic online evaluation and calibration for market interpretations.

The loop is deliberately conservative: it never rewrites old observations, never
uses future data at prediction time, and only changes bounded diagnostic weights
from settled, timestamped outcomes. It is calibration, not autonomous trading.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

HISTORY_DIR = "history"
PREDICTIONS_FILE = "prediction_ledger.json"
EVALUATIONS_FILE = "evaluation_results.json"
RUNS_FILE = "evaluation_runs.json"
CALIBRATION_FILE = "model_calibration.json"
CONFIDENCE_THRESHOLD = 80
MIN_CALIBRATION_SAMPLES = 10
HORIZON_BARS = 3
WEIGHT_STEP = 0.02
WEIGHT_MIN = 0.50
WEIGHT_MAX = 1.50
DEFAULT_WEIGHTS = {
    "price_action": 1.00,
    "oi_context": 1.00,
    "volume_confirmation": 1.00,
    "funding_context": 1.00,
    "btc_overlay": 1.00,
    "positioning_context": 1.00,
}


def _path(name):
    return os.path.join(HISTORY_DIR, name)


def _load(name, default):
    try:
        with open(_path(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _save(name, value):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path(name))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    if "T" not in text:
        text = text.replace(" ", "T") + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _direction_for(analysis):
    price_dir = analysis.get("price_dir")
    if price_dir == "Rising":
        return "up"
    if price_dir == "Falling":
        return "down"
    return "flat"


def _components(analysis):
    """Record which evidence families were available at decision time."""
    return {
        "price_action": analysis.get("price_dir") not in (None, "Unknown"),
        "oi_context": analysis.get("oi_dir") not in (None, "Unknown"),
        "volume_confirmation": analysis.get("volume_dir") not in (None, "Unknown"),
        "funding_context": analysis.get("funding_rate") is not None,
        "btc_overlay": analysis.get("btc_dependency") not in (None, "Unknown"),
        "positioning_context": analysis.get("positioning_pressure") is not None,
    }


def _current_calibration():
    value = _load(CALIBRATION_FILE, {})
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(value.get("weights") or {})
    return {
        "version": value.get("version", 1),
        "weights": weights,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "min_samples": MIN_CALIBRATION_SAMPLES,
        "updated_at": value.get("updated_at"),
    }


def get_calibration():
    """Return the current bounded calibration snapshot for model consumers."""
    return _current_calibration()


def _later_observations(symbol, issued_at, histories):
    rows = histories.get(symbol) or []
    issued = _parse_time(issued_at)
    if issued is None:
        return []
    out = []
    for row in rows:
        t = _parse_time(row.get("date"))
        if t and t > issued and row.get("price") is not None:
            out.append(row)
    return out


def _settle_pending(predictions, evaluations, histories):
    settled_ids = {x.get("prediction_id") for x in evaluations}
    newly_settled = []
    for prediction in predictions:
        pid = prediction.get("prediction_id")
        if pid in settled_ids:
            continue
        later = _later_observations(prediction.get("symbol"), prediction.get("issued_at"), histories)
        if len(later) < HORIZON_BARS:
            continue
        entry = prediction.get("entry_price")
        target = later[HORIZON_BARS - 1].get("price")
        if not entry or not target:
            continue
        returns = [((r["price"] - entry) / entry) * 100 for r in later[:HORIZON_BARS] if r.get("price")]
        forward_return = ((target - entry) / entry) * 100
        actual_direction = "up" if forward_return > 0.15 else "down" if forward_return < -0.15 else "flat"
        adverse_move = min(returns) if returns else 0.0
        predicted_risk = prediction.get("risk_level") in ("High", "Very High")
        risk_hit = adverse_move <= -1.0
        outcome = {
            "prediction_id": pid,
            "settled_at": _now(),
            "symbol": prediction.get("symbol"),
            "issued_at": prediction.get("issued_at"),
            "horizon_bars": HORIZON_BARS,
            "predicted_direction": prediction.get("predicted_direction"),
            "actual_direction": actual_direction,
            "direction_hit": prediction.get("predicted_direction") == actual_direction,
            "predicted_risk_level": prediction.get("risk_level"),
            "risk_event_observed": risk_hit,
            "risk_signal_hit": (predicted_risk == risk_hit),
            "forward_return_pct": round(forward_return, 4),
            "max_adverse_move_pct": round(adverse_move, 4),
            "components": prediction.get("components", {}),
        }
        evaluations.append(outcome)
        newly_settled.append(outcome)
    return newly_settled


def _update_weights(weights, newly_settled):
    counts = {k: {"n": 0, "hits": 0} for k in weights}
    for outcome in newly_settled:
        hit = bool(outcome.get("direction_hit"))
        for key, present in (outcome.get("components") or {}).items():
            if present and key in counts:
                counts[key]["n"] += 1
                counts[key]["hits"] += int(hit)
    updates = []
    for key, stat in counts.items():
        if stat["n"] < 1:
            continue
        delta = WEIGHT_STEP if stat["hits"] / stat["n"] >= 0.5 else -WEIGHT_STEP
        old = float(weights[key])
        new = round(max(WEIGHT_MIN, min(WEIGHT_MAX, old + delta)), 4)
        weights[key] = new
        updates.append({"feature": key, "old": old, "new": new, "samples": stat["n"], "hits": stat["hits"]})
    return updates


def _metrics(evaluations):
    if not evaluations:
        return {"samples": 0, "direction_accuracy": None, "risk_accuracy": None, "confidence": 0}
    direction = sum(bool(x.get("direction_hit")) for x in evaluations)
    risk = sum(bool(x.get("risk_signal_hit")) for x in evaluations)
    n = len(evaluations)
    direction_accuracy = round(direction * 100 / n, 1)
    risk_accuracy = round(risk * 100 / n, 1)
    blended = round((direction_accuracy * 0.65) + (risk_accuracy * 0.35), 1)
    confidence = blended if n >= MIN_CALIBRATION_SAMPLES else min(blended, 65.0)
    return {
        "samples": n,
        "direction_accuracy": direction_accuracy,
        "risk_accuracy": risk_accuracy,
        "confidence": confidence,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "threshold_reached": bool(n >= MIN_CALIBRATION_SAMPLES and confidence >= CONFIDENCE_THRESHOLD),
    }


def run_cycle(run_id, timeframe, symbol_data, analyses):
    """Settle old forecasts, record this run, and create new forecasts."""
    predictions = _load(PREDICTIONS_FILE, [])
    evaluations = _load(EVALUATIONS_FILE, [])
    runs = _load(RUNS_FILE, [])
    calibration = _current_calibration()
    histories = {d.get("symbol"): d.get("history", []) for d in symbol_data if d}
    newly_settled = _settle_pending(predictions, evaluations, histories)
    updates = _update_weights(calibration["weights"], newly_settled)

    input_snapshot = []
    by_symbol = {d.get("symbol"): d for d in symbol_data}
    for analysis in analyses:
        symbol = analysis.get("symbol")
        data = by_symbol.get(symbol, {})
        issued_at = analysis.get("evaluated_at") or _now()
        combined = {**analysis, **data, **(data.get("directions") or {})}
        combined["btc_dependency"] = (analysis.get("current_relationship") or {}).get("btc_dependency")
        components = _components(combined)
        prediction = {
            "prediction_id": f"{run_id}:{symbol}:{uuid.uuid4().hex[:8]}",
            "run_id": run_id,
            "issued_at": issued_at,
            "symbol": symbol,
            "timeframe": timeframe,
            "entry_price": data.get("price"),
            "predicted_direction": _direction_for(combined),
            "risk_level": (analysis.get("sudden_drop_risk") or {}).get("level"),
            "predicted_squeeze_risk": bool((analysis.get("positioning") or {}).get("crowded_short")),
            "components": components,
            "weights": dict(calibration["weights"]),
        }
        predictions.append(prediction)
        input_snapshot.append({
            "symbol": symbol,
            "inputs": {k: data.get(k) for k in ("price", "volume", "open_interest", "funding_rate", "price_change_pct", "oi_change_pct", "rsi")},
            "directions": data.get("directions", {}),
            "output": {"primary_mechanism": (analysis.get("likely_mechanism") or {}).get("primary"), "risk_level": prediction["risk_level"]},
            "reasoning": (analysis.get("likely_mechanism") or {}).get("reasoning", []),
        })
    metrics = _metrics(evaluations)
    run = {
        "run_id": run_id,
        "evaluated_at": _now(),
        "timeframe": timeframe,
        "model_version": "deterministic-mechanism-v3-online-calibration",
        "sources": {"price_volume": "Binance spot", "oi_funding": "OKX perpetual", "sentiment": "alternative.me"},
        "inputs_outputs": input_snapshot,
        "newly_settled_count": len(newly_settled),
        "calibration_updates": updates,
        "metrics_after_run": metrics,
        "weights": dict(calibration["weights"]),
    }
    runs.append(run)
    calibration.update({"weights": calibration["weights"], "updated_at": _now(), "last_run_id": run_id, "metrics": metrics})
    _save(PREDICTIONS_FILE, predictions)
    _save(EVALUATIONS_FILE, evaluations)
    _save(RUNS_FILE, runs)
    _save(CALIBRATION_FILE, calibration)
    return {"run_id": run_id, "newly_settled": len(newly_settled), "metrics": metrics, "updates": updates}
