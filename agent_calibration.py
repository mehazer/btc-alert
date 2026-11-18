"""Live self-calibration for market agent signals and technical states.

Why this exists:
- Scorecards were updating, but weights were not applied back into agents.
- Legacy model_calibration.json stopped updating after 2026-11-09.

This loop is conservative:
- Point-in-time: settle only after horizon bars exist
- Missing inputs stay null (never imputed)
- Weights move in small steps and stay bounded
- No LLM, no auto-trading, no promotion bypass
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HISTORY_DIR = Path(os.environ.get("HISTORY_DIR", "history"))
CALIB_PATH = HISTORY_DIR / "agent_calibration.json"
LEDGER_PATH = HISTORY_DIR / "market_agent_ledger.json"
BTC_PATH = HISTORY_DIR / "BTCUSDT.json"
V2_CARDS_PATH = HISTORY_DIR / "intelligence_v2" / "agent_scorecards.json"

HORIZON_BARS = 3
WEIGHT_STEP = 0.03
WEIGHT_MIN = 0.40
WEIGHT_MAX = 1.60
MIN_SAMPLES_FOR_TRUST = 12

DEFAULT_SIGNAL_WEIGHTS = {
    "rsi": 1.0,
    "fear_greed": 1.0,
    "funding": 1.0,
    "oi_price": 1.0,
    "price": 1.0,
    "news": 1.0,
    "social": 0.85,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    if "T" not in text:
        text = text.replace(" ", "T") + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_calibration() -> dict[str, Any]:
    data = _load(CALIB_PATH, {}) or {}
    weights = dict(DEFAULT_SIGNAL_WEIGHTS)
    weights.update(data.get("signal_weights") or {})
    return {
        "version": data.get("version", 1),
        "signal_weights": weights,
        "state_multipliers": data.get("state_multipliers") or {},
        "metrics": data.get("metrics") or {},
        "history": data.get("history") or [],
        "updated_at": data.get("updated_at"),
        "notes": data.get("notes") or [],
    }


def _direction_from_state(state: str | None) -> str:
    s = (state or "").upper()
    if s in {"BULLISH", "OVERHEATED"}:
        return "up"
    if s in {"BEARISH", "FEAR"}:
        return "down"
    return "flat"


def _active_signals(inputs: dict) -> dict[str, bool]:
    return {
        "rsi": inputs.get("rsi") is not None,
        "fear_greed": inputs.get("fear_greed_value") is not None,
        "funding": inputs.get("funding_rate") is not None,
        "oi_price": inputs.get("oi_change_pct") is not None and inputs.get("price_change_pct") is not None,
        "price": inputs.get("price_change_pct") is not None,
        "news": inputs.get("news_sentiment") is not None,
        "social": inputs.get("social_sentiment") is not None,
    }


def record_prediction(evaluation: dict, market_payload: dict) -> dict:
    """Append this run's market_agent call to the prediction ledger."""
    ledger = _load(LEDGER_PATH, []) or []
    if not isinstance(ledger, list):
        ledger = []
    inputs = evaluation.get("inputs") or {
        "rsi": market_payload.get("rsi"),
        "fear_greed_value": market_payload.get("fear_greed_value"),
        "funding_rate": market_payload.get("funding_rate"),
        "oi_change_pct": market_payload.get("oi_change_pct"),
        "price_change_pct": market_payload.get("price_change_pct"),
        "news_sentiment": market_payload.get("news_sentiment"),
        "social_sentiment": market_payload.get("social_sentiment"),
    }
    row = {
        "prediction_id": f"ma-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "issued_at": evaluation.get("evaluated_at") or _now(),
        "entry_price": market_payload.get("price"),
        "state": evaluation.get("state"),
        "predicted_direction": _direction_from_state(evaluation.get("state")),
        "bias_score": evaluation.get("bias_score"),
        "confidence": evaluation.get("confidence"),
        "active_signals": _active_signals(inputs),
        "inputs": inputs,
        "settled": False,
    }
    ledger.append(row)
    # Keep last 400 predictions
    ledger = ledger[-400:]
    _save(LEDGER_PATH, ledger)
    return row


def _settle(ledger: list, btc_rows: list) -> list:
    newly = []
    for pred in ledger:
        if pred.get("settled"):
            continue
        issued = _parse_time(pred.get("issued_at"))
        entry = pred.get("entry_price")
        if issued is None or entry in (None, 0):
            continue
        later = []
        for row in btc_rows:
            t = _parse_time(row.get("date") or row.get("retrieved_at"))
            if t and t > issued and row.get("price") is not None:
                later.append(row)
        if len(later) < HORIZON_BARS:
            continue
        target = later[HORIZON_BARS - 1]["price"]
        fwd = (float(target) - float(entry)) / float(entry) * 100.0
        actual = "up" if fwd > 0.15 else "down" if fwd < -0.15 else "flat"
        predicted = pred.get("predicted_direction") or "flat"
        # Flat predictions: hit if market stayed roughly flat
        if predicted == "flat":
            hit = actual == "flat"
        else:
            hit = predicted == actual
        pred["settled"] = True
        pred["settled_at"] = _now()
        pred["actual_direction"] = actual
        pred["forward_return_pct"] = round(fwd, 4)
        pred["direction_hit"] = bool(hit)
        newly.append(pred)
    return newly


def _update_signal_weights(weights: dict, newly: list) -> list:
    stats = {k: {"n": 0, "hits": 0} for k in weights}
    for pred in newly:
        hit = bool(pred.get("direction_hit"))
        for key, present in (pred.get("active_signals") or {}).items():
            if present and key in stats:
                stats[key]["n"] += 1
                stats[key]["hits"] += int(hit)
    updates = []
    for key, st in stats.items():
        if st["n"] < 1:
            continue
        rate = st["hits"] / st["n"]
        # Neutral-ish hit rate keeps weight; strong miss reduces; strong hit increases
        if rate >= 0.55:
            delta = WEIGHT_STEP
        elif rate <= 0.40:
            delta = -WEIGHT_STEP
        else:
            delta = 0.0
        old = float(weights[key])
        new = round(max(WEIGHT_MIN, min(WEIGHT_MAX, old + delta)), 4)
        weights[key] = new
        if new != old:
            updates.append({
                "signal": key,
                "old": old,
                "new": new,
                "samples": st["n"],
                "hits": st["hits"],
                "hit_rate": round(rate * 100, 1),
            })
    return updates


def _state_multipliers_from_v2() -> dict:
    """Translate technical by_state accuracy into confidence multipliers."""
    cards = _load(V2_CARDS_PATH, {}) or {}
    tech = ((cards.get("agents") or {}).get("technical") or {})
    by_state = tech.get("by_state") or {}
    multipliers = {}
    for state, row in by_state.items():
        n = int(row.get("n") or 0)
        acc = row.get("accuracy_pct")
        if n < 8 or acc is None:
            continue
        # Map accuracy to multiplier around 1.0
        # 50% -> 1.0, 25% -> 0.7, 75% -> 1.2 (bounded)
        mult = 0.7 + (float(acc) / 100.0) * 0.6
        multipliers[state] = {
            "multiplier": round(max(0.55, min(1.35, mult)), 3),
            "samples": n,
            "accuracy_pct": float(acc),
            "advice": (
                "downweight" if float(acc) < 40 else
                "upweight" if float(acc) >= 60 else
                "neutral"
            ),
        }
    return multipliers


def _metrics(ledger: list) -> dict:
    settled = [x for x in ledger if x.get("settled")]
    if not settled:
        return {
            "samples": 0,
            "direction_accuracy": None,
            "trusted": False,
            "pending": sum(1 for x in ledger if not x.get("settled")),
        }
    hits = sum(1 for x in settled if x.get("direction_hit"))
    n = len(settled)
    acc = round(hits * 100 / n, 1)
    return {
        "samples": n,
        "direction_accuracy": acc,
        "hits": hits,
        "trusted": n >= MIN_SAMPLES_FOR_TRUST,
        "pending": sum(1 for x in ledger if not x.get("settled")),
        "horizon_bars": HORIZON_BARS,
    }


def run_cycle(evaluation: dict | None = None, market_payload: dict | None = None) -> dict:
    """Settle pending market-agent calls, update weights, optionally record this run."""
    calib = load_calibration()
    ledger = _load(LEDGER_PATH, []) or []
    if not isinstance(ledger, list):
        ledger = []
    btc_rows = _load(BTC_PATH, []) or []
    if not isinstance(btc_rows, list):
        btc_rows = []

    newly = _settle(ledger, btc_rows)
    updates = _update_signal_weights(calib["signal_weights"], newly) if newly else []
    state_mult = _state_multipliers_from_v2()

    if evaluation is not None and market_payload is not None:
        pred = {
            "prediction_id": f"ma-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "issued_at": evaluation.get("evaluated_at") or _now(),
            "entry_price": market_payload.get("price"),
            "state": evaluation.get("state"),
            "predicted_direction": _direction_from_state(evaluation.get("state")),
            "bias_score": evaluation.get("bias_score"),
            "confidence": evaluation.get("confidence"),
            "active_signals": _active_signals(evaluation.get("inputs") or {}),
            "inputs": evaluation.get("inputs") or {},
            "settled": False,
        }
        ledger.append(pred)

    ledger = ledger[-400:]
    metrics = _metrics(ledger)

    history = list(calib.get("history") or [])
    if updates or newly:
        history.append({
            "at": _now(),
            "newly_settled": len(newly),
            "weight_updates": updates,
            "metrics": metrics,
        })
    history = history[-50:]

    notes = []
    if metrics.get("samples", 0) < MIN_SAMPLES_FOR_TRUST:
        notes.append(
            f"Need >={MIN_SAMPLES_FOR_TRUST} settled market-agent samples before treating weights as trusted "
            f"(have {metrics.get('samples', 0)})."
        )
    if state_mult:
        weak = [k for k, v in state_mult.items() if v.get("advice") == "downweight"]
        if weak:
            notes.append(f"Technical states underperforming: {', '.join(weak)}.")

    out = {
        "version": 1,
        "updated_at": _now(),
        "signal_weights": calib["signal_weights"],
        "state_multipliers": state_mult,
        "metrics": metrics,
        "last_cycle": {
            "newly_settled": len(newly),
            "weight_updates": updates,
        },
        "history": history,
        "notes": notes,
        "method": "market_agent horizon settle + technical by_state multipliers",
    }
    _save(LEDGER_PATH, ledger)
    _save(CALIB_PATH, out)
    return out


if __name__ == "__main__":
    result = run_cycle()
    print(json.dumps({
        "metrics": result.get("metrics"),
        "weights": result.get("signal_weights"),
        "last_cycle": result.get("last_cycle"),
        "notes": result.get("notes"),
    }, indent=2))
