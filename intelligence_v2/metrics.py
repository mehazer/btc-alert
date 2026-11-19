"""Measurement primitives for v2 evaluation.

Three problems this module exists to fix, all of them measurement problems:

1. A raw hit rate on a directional signal cannot distinguish skill from market
   drift. Every accuracy number here is reported next to the base rate it has to
   beat, plus naive baselines (always up, always down, persistence, EMA slope).
2. Labelling every non-zero forward return as up or down turns 4h noise into
   wrong calls. Labels use a volatility-scaled neutral band, so a move smaller
   than the noise floor is reported as neutral instead of scored as an error.
3. A confidence number that has never been compared to observed hit rates is an
   assertion. ``reliability_table`` produces the observed rate for each stated
   probability, with Brier score, log loss and expected calibration error.

Design rules:
- Pure functions only: no I/O, no clock, no global state.
- Point-in-time safe by construction. Callers pass only bars available at the
  decision timestamp, so nothing here can look ahead.
"""
from __future__ import annotations

import math
from statistics import pstdev
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_NEUTRAL_BAND_K = 0.5
DEFAULT_VOL_LOOKBACK = 20
DEFAULT_EMA_SPAN = 20
NEUTRAL_LABEL = "neutral"
DIRECTIONAL_LABELS = ("up", "down")


def pct_returns(closes: Sequence[float]) -> List[float]:
    """Simple percentage returns between consecutive closes."""
    series: List[float] = []
    for previous, current in zip(closes, closes[1:]):
        if previous:
            series.append((float(current) - float(previous)) / float(previous) * 100.0)
    return series


def volatility_pct(closes: Sequence[float], lookback: int = DEFAULT_VOL_LOOKBACK) -> Optional[float]:
    """Per-bar volatility in percent, from the trailing ``lookback`` returns."""
    if not closes or len(closes) < 3:
        return None
    returns = pct_returns(closes)
    if lookback and lookback > 0:
        returns = returns[-lookback:]
    if len(returns) < 2:
        return None
    return float(pstdev(returns))


def neutral_band_pct(
    volatility: Optional[float],
    horizon_bars: int,
    k: float = DEFAULT_NEUTRAL_BAND_K,
) -> Optional[float]:
    """Half-width of the neutral band, in percent, for one horizon.

    Scaled by ``sqrt(horizon)`` because volatility accumulates with the square
    root of time. ``k`` is the multiple of one-sigma move treated as noise.
    """
    if volatility is None or volatility <= 0 or horizon_bars <= 0 or k <= 0:
        return None
    return round(k * float(volatility) * math.sqrt(horizon_bars), 6)


def label_return(forward_return_pct: Optional[float], band_pct: Optional[float]) -> Optional[str]:
    """Three-class outcome label: up, down or neutral.

    With no band available the label degrades to the historical sign rule, so
    callers that cannot compute volatility keep working.
    """
    if forward_return_pct is None:
        return None
    value = float(forward_return_pct)
    if band_pct is None:
        if value > 0:
            return "up"
        if value < 0:
            return "down"
        return NEUTRAL_LABEL
    if value > band_pct:
        return "up"
    if value < -band_pct:
        return "down"
    return NEUTRAL_LABEL


def direction_correct(signal_direction: Optional[str], label: Optional[str]) -> Optional[bool]:
    """Was a directional call correct? ``None`` when the move was inside the band."""
    if signal_direction not in DIRECTIONAL_LABELS or label in (None, NEUTRAL_LABEL):
        return None
    return (label == "up") == (signal_direction == "up")


def accuracy_stats(labels: Sequence[Optional[str]], predictions: Sequence[Optional[str]]) -> Dict[str, Any]:
    """Accuracy summary that keeps neutral outcomes visible.

    Neutral truths are counted in ``coverage`` but excluded from the hit rate,
    because a move smaller than the noise floor is not evidence either way.
    """
    pairs = [
        (label, prediction)
        for label, prediction in zip(labels, predictions)
        if label in ("up", "down", NEUTRAL_LABEL) and prediction in DIRECTIONAL_LABELS
    ]
    neutral = [pair for pair in pairs if pair[0] == NEUTRAL_LABEL]
    directional = [pair for pair in pairs if pair[0] in DIRECTIONAL_LABELS]
    hits = sum(1 for label, prediction in directional if label == prediction)
    up_rows = [pair for pair in directional if pair[0] == "up"]
    down_rows = [pair for pair in directional if pair[0] == "down"]
    up_hits = sum(1 for label, prediction in up_rows if prediction == "up")
    down_hits = sum(1 for label, prediction in down_rows if prediction == "down")
    recall_up = (up_hits / len(up_rows) * 100.0) if up_rows else None
    recall_down = (down_hits / len(down_rows) * 100.0) if down_rows else None
    recalls = [value for value in (recall_up, recall_down) if value is not None]
    total = len(pairs)
    return {
        "n": total,
        "directional": len(directional),
        "hits": hits,
        "accuracy_pct": round(hits / len(directional) * 100.0, 2) if directional else None,
        "neutral": len(neutral),
        "neutral_rate_pct": round(len(neutral) / total * 100.0, 2) if total else None,
        "coverage_pct": round(len(directional) / total * 100.0, 2) if total else None,
        "base_rate_up_pct": round(len(up_rows) / len(directional) * 100.0, 2) if directional else None,
        "recall_up_pct": round(recall_up, 2) if recall_up is not None else None,
        "recall_down_pct": round(recall_down, 2) if recall_down is not None else None,
        "balanced_accuracy_pct": round(sum(recalls) / len(recalls), 2) if recalls else None,
    }


def skill_score(accuracy_pct: Optional[float], baseline_pct: Optional[float]) -> Optional[float]:
    """Accuracy above a baseline, rescaled to the room the baseline left (0-100)."""
    if accuracy_pct is None or baseline_pct is None:
        return None
    if baseline_pct >= 100.0:
        return None
    return round((accuracy_pct - baseline_pct) / (100.0 - baseline_pct) * 100.0, 2)


def always_up_stats(labels: Sequence[Optional[str]]) -> Dict[str, Any]:
    """What predicting 'up' on every row would have scored on the same sample."""
    usable = [label for label in labels if label in ("up", "down")]
    hits = sum(1 for label in usable if label == "up")
    accuracy = round(hits / len(usable) * 100.0, 2) if usable else None
    return {"name": "always_up", "n": len(usable), "accuracy_pct": accuracy}


def always_down_stats(labels: Sequence[Optional[str]]) -> Dict[str, Any]:
    """What predicting 'down' on every row would have scored on the same sample."""
    usable = [label for label in labels if label in ("up", "down")]
    hits = sum(1 for label in usable if label == "down")
    accuracy = round(hits / len(usable) * 100.0, 2) if usable else None
    return {"name": "always_down", "n": len(usable), "accuracy_pct": accuracy}


def ema(closes: Sequence[float], span: int = DEFAULT_EMA_SPAN) -> Optional[float]:
    """Exponential moving average of a price series."""
    if not closes:
        return None
    multiplier = 2.0 / (span + 1.0)
    value = float(closes[0])
    for price in closes[1:]:
        value = (float(price) - value) * multiplier + value
    return value


def naive_directions(closes: Sequence[float], span: int = DEFAULT_EMA_SPAN) -> Dict[str, Optional[str]]:
    """Naive next-move predictions from bars available at decision time.

    ``persistence`` continues the last completed bar's direction; ``ema_slope``
    follows the EMA's own change over the final bar. Both are one-line rules
    that on a drifting market beat most elaborate signals, which is exactly why
    they belong on the scorecard.
    """
    result: Dict[str, Optional[str]] = {"persistence": None, "ema_slope": None}
    if len(closes) >= 2:
        result["persistence"] = "up" if float(closes[-1]) > float(closes[-2]) else "down"
    if len(closes) >= span + 1 and len(closes) >= 2:
        current = ema(closes, span)
        previous = ema(closes[:-1], span)
        if current is not None and previous is not None:
            if current > previous:
                result["ema_slope"] = "up"
            elif current < previous:
                result["ema_slope"] = "down"
    return result


def regime_summary(closes: Sequence[float]) -> Dict[str, Any]:
    """Describe the sample window: drift direction, drift strength, volatility.

    ``drift_z`` is the window return divided by one-sigma of the window, so it
    says how large the drift is compared with the noise that produced it.
    """
    if not closes or len(closes) < 3:
        return {"window_return_pct": None, "drift_z": None, "sigma_pct": None, "label": "unknown"}
    first, last = float(closes[0]), float(closes[-1])
    window_return = (last - first) / first * 100.0 if first else None
    sigma = volatility_pct(closes, lookback=0)
    bars = max(len(closes) - 1, 1)
    if sigma is None or sigma <= 0 or window_return is None:
        return {"window_return_pct": round(window_return, 2) if window_return is not None else None,
                "drift_z": None, "sigma_pct": None, "label": "unknown"}
    drift_z = window_return / (sigma * math.sqrt(bars))
    if abs(drift_z) < 0.5:
        label = "range"
    else:
        label = "trend_up" if drift_z > 0 else "trend_down"
    return {
        "window_return_pct": round(window_return, 2),
        "drift_z": round(drift_z, 3),
        "sigma_pct": round(sigma, 4),
        "label": label,
    }


def reliability_table(
    pairs: Iterable[Tuple[Optional[float], Optional[bool]]],
    bins: int = 5,
) -> Dict[str, Any]:
    """Stated probability versus observed outcome.

    ``pairs`` is ``(stated_probability, was_correct)``. Returns per-bucket hit
    rates with sample counts, plus Brier score, log loss and expected
    calibration error. A probability that has never been checked this way is an
    assumption, not a confidence.
    """
    usable = [
        (min(max(float(probability), 0.0), 1.0), 1.0 if outcome else 0.0)
        for probability, outcome in pairs
        if probability is not None and outcome is not None
    ]
    if not usable:
        return {"n": 0, "buckets": [], "brier_score": None, "log_loss": None, "ece": None,
                "mean_stated": None, "observed_rate_pct": None}
    width = 1.0 / max(bins, 1)
    buckets: Dict[int, List[Tuple[float, float]]] = {}
    for probability, outcome in usable:
        index = min(int(probability / width), bins - 1)
        buckets.setdefault(index, []).append((probability, outcome))
    rows = []
    ece = 0.0
    for index in sorted(buckets):
        members = buckets[index]
        stated = sum(p for p, _ in members) / len(members)
        observed = sum(y for _, y in members) / len(members)
        ece += abs(observed - stated) * len(members) / len(usable)
        rows.append({
            "bucket_low": round(index * width, 3),
            "bucket_high": round((index + 1) * width, 3),
            "n": len(members),
            "mean_stated": round(stated, 3),
            "observed_rate_pct": round(observed * 100.0, 2),
            "gap_pct": round((observed - stated) * 100.0, 2),
        })
    brier = sum((p - y) ** 2 for p, y in usable) / len(usable)
    clipped = [(min(max(p, 1e-6), 1 - 1e-6), y) for p, y in usable]
    log_loss = -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in clipped) / len(clipped)
    return {
        "n": len(usable),
        "buckets": rows,
        "brier_score": round(brier, 4),
        "log_loss": round(log_loss, 4),
        "ece": round(ece, 4),
        "mean_stated": round(sum(p for p, _ in usable) / len(usable), 3),
        "observed_rate_pct": round(sum(y for _, y in usable) / len(usable) * 100.0, 2),
    }
