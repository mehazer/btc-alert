"""Recompute v2 measurement with baselines, banded labels and reliability.

This is a read-only reporter. It never rewrites the live ledgers or scorecards;
it produces ``measurement_report.json`` so the old numbers and the corrected
numbers can be compared on identical data.

Usage:
    python -m intelligence_v2.measure --history-dir history
    python -m intelligence_v2.measure --history-dir history --band-k 0.75
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import metrics
from .evaluation import closes_until, load_history_rows
from .ledger import (
    DECISIONS_FILE,
    EVALUATIONS_FILE,
    HORIZONS,
    _baselines_block,
    _measurement_block,
    ledger_dir,
)
from .forecast_eval import EVAL_NAME as FORECAST_EVAL_NAME

REPORT_NAME = "measurement_report.json"


def _read(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _sign_label(value: Optional[float]) -> Optional[str]:
    if value is None or value == 0:
        return None
    return "up" if value > 0 else "down"


def _enrich(rows: List[dict], history_dir: str, band_k: float) -> List[dict]:
    """Recompute the band, the three-class label and correctness for every row."""
    cache: Dict[str, List[dict]] = {}
    enriched: List[dict] = []
    for row in rows:
        item = dict(row)
        symbol = str(item.get("symbol") or "")
        closes: List[float] = []
        if symbol and history_dir:
            if symbol not in cache:
                cache[symbol] = load_history_rows(history_dir, symbol)
            closes = closes_until(cache[symbol], str(item.get("decision_timestamp") or ""))
        band = metrics.neutral_band_pct(
            metrics.volatility_pct(closes) if closes else None,
            int(item.get("horizon_bars") or 0),
            band_k,
        )
        label = metrics.label_return(item.get("forward_return_pct"), band)
        item["neutral_band_pct"] = band
        item["outcome_label"] = label
        item["direction_correct"] = metrics.direction_correct(item.get("signal_direction"), label)
        item["sign_label"] = _sign_label(item.get("forward_return_pct"))
        enriched.append(item)
    return enriched


def _legacy_vs_banded(rows: List[dict]) -> Dict[str, Any]:
    settled = [
        r for r in rows
        if r.get("status") == "settled"
        and (r.get("direction_correct") is not None or r.get("sign_label") is not None)
    ]
    sign_pairs = [(r.get("sign_label"), r.get("signal_direction")) for r in settled if r.get("sign_label")]
    sign_stats = metrics.accuracy_stats([p[0] for p in sign_pairs], [p[1] for p in sign_pairs])
    band_stats = metrics.accuracy_stats(
        [r.get("outcome_label") for r in settled],
        [r.get("signal_direction") for r in settled],
    )
    neutral = sum(1 for r in settled if r.get("outcome_label") == metrics.NEUTRAL_LABEL)
    return {
        "legacy_sign_labels": sign_stats,
        "banded_labels": band_stats,
        "neutral_moves": neutral,
        "delta_accuracy_pct": (
            round(band_stats["accuracy_pct"] - sign_stats["accuracy_pct"], 2)
            if band_stats.get("accuracy_pct") is not None and sign_stats.get("accuracy_pct") is not None
            else None
        ),
        "note": "Legacy labels call every non-zero move up or down; banded labels exclude moves inside the noise floor.",
    }


def _per_horizon(rows: List[dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for horizon in HORIZONS:
        subset = [r for r in rows if int(r.get("horizon_bars") or 0) == horizon and r.get("status") == "settled"]
        if not subset:
            continue
        banded = metrics.accuracy_stats(
            [r.get("outcome_label") for r in subset],
            [r.get("signal_direction") for r in subset],
        )
        sign = metrics.accuracy_stats(
            [r.get("sign_label") for r in subset],
            [r.get("signal_direction") for r in subset],
        )
        up_share = (
            round(sum(1 for r in subset if r.get("outcome_label") == "up") / len([r for r in subset if r.get("outcome_label") in ("up", "down")]) * 100.0, 2)
            if any(r.get("outcome_label") in ("up", "down") for r in subset) else None
        )
        out[str(horizon)] = {
            "settled": len(subset),
            "banded_accuracy_pct": banded.get("accuracy_pct"),
            "banded_balanced_accuracy_pct": banded.get("balanced_accuracy_pct"),
            "legacy_accuracy_pct": sign.get("accuracy_pct"),
            "directional_n": banded.get("directional"),
            "neutral_n": banded.get("neutral"),
            "up_share_of_directional_pct": up_share,
            "base_rate_up_pct": banded.get("base_rate_up_pct"),
        }
    return out


def _forecast_reliability(history_dir: str, band_k: float) -> Dict[str, Any]:
    evaluations = _read(os.path.join(ledger_dir(history_dir), FORECAST_EVAL_NAME), [])
    settled = [e for e in evaluations if isinstance(e, dict) and e.get("status") == "settled"]
    pairs: List[tuple] = []
    for row in settled:
        low, high = row.get("probability_low"), row.get("probability_high")
        if low is None or high is None:
            continue
        correct = None
        if row.get("outcome_label") is not None:
            correct = metrics.direction_correct(row.get("direction"), row.get("outcome_label"))
        elif row.get("direction_correct") is not None:
            correct = row.get("direction_correct")
        if correct is None:
            continue
        pairs.append(((float(low) + float(high)) / 2.0, bool(correct)))
    table = metrics.reliability_table(pairs)
    table["settled_rows"] = len(settled)
    table["scored_rows"] = len(pairs)
    table["band_k"] = band_k
    return table


def build_report(history_dir: str, band_k: Optional[float] = None) -> Dict[str, Any]:
    k = metrics.DEFAULT_NEUTRAL_BAND_K if band_k is None else float(band_k)
    evaluations = _read(os.path.join(ledger_dir(history_dir), EVALUATIONS_FILE), [])
    decisions = _read(os.path.join(ledger_dir(history_dir), DECISIONS_FILE), [])
    if not isinstance(evaluations, list):
        evaluations = []
    if not isinstance(decisions, list):
        decisions = []
    enriched = _enrich(evaluations, history_dir, k)
    baselines = _baselines_block(enriched, history_dir, k)
    measurement = _measurement_block(enriched, decisions, history_dir, k)
    confidence = metrics.reliability_table([
        (int(r.get("confidence")) / 100.0, bool(r.get("direction_correct")))
        for r in enriched
        if r.get("direction_correct") is not None and r.get("confidence") is not None
    ])
    return {
        "agent": "measurement-report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history_dir": history_dir,
        "band_k": k,
        "rows": len(enriched),
        "measurement": measurement,
        "accuracy": _legacy_vs_banded(enriched),
        "by_horizon": _per_horizon(enriched),
        "baselines": baselines,
        "confidence_reliability": confidence,
        "forecast_reliability": _forecast_reliability(history_dir, k),
        "reading_order": [
            "1. measurement.sample_window and measurement.regime: what period and market is this?",
            "2. measurement.directional_coverage_pct: how much of the sample is even scored?",
            "3. baselines.model vs baselines.best_baseline: does the model beat a one-line rule?",
            "4. accuracy.banded vs accuracy.legacy: how much was noise being counted as error?",
            "5. confidence_reliability and forecast_reliability: does stated confidence match observed rates?",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recompute v2 measurement with baselines and calibration")
    parser.add_argument("--history-dir", default="history")
    parser.add_argument("--band-k", type=float, default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    report = build_report(args.history_dir, args.band_k)
    output = args.output or os.path.join(ledger_dir(args.history_dir), REPORT_NAME)
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    print(json.dumps({
        "output": output,
        "rows": report["rows"],
        "band_k": report["band_k"],
        "legacy_accuracy_pct": report["accuracy"]["legacy_sign_labels"].get("accuracy_pct"),
        "banded_accuracy_pct": report["accuracy"]["banded_labels"].get("accuracy_pct"),
        "banded_balanced_accuracy_pct": report["accuracy"]["banded_labels"].get("balanced_accuracy_pct"),
        "model_accuracy_pct": (report["baselines"].get("model") or {}).get("accuracy_pct"),
        "best_baseline": report["baselines"].get("best_baseline"),
        "best_baseline_accuracy_pct": next(
            (b.get("accuracy_pct") for b in report["baselines"].get("baselines", [])
             if b.get("name") == report["baselines"].get("best_baseline")), None,
        ),
        "model_skill_vs_best_baseline_pct": report["baselines"].get("model_skill_vs_best_baseline_pct"),
        "confidence_brier": report["confidence_reliability"].get("brier_score"),
        "forecast_brier": report["forecast_reliability"].get("brier_score"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
