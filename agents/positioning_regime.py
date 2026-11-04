"""
agents/positioning_regime.py
----------------------------
Deterministic helpers for:
  3. Funding + OI divergence extremes (positioning pressure)
  5. Regime filter (volatility + trend strength)

All pure functions; no I/O, no LLM.
"""

from datetime import datetime, timezone
import math


def _safe_pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return ((a - b) / b) * 100


def _percentile_rank(value, series):
    """Return 0-100 percentile rank of value within series (higher = larger)."""
    clean = [float(x) for x in series if x is not None]
    if not clean or value is None:
        return None
    below = sum(1 for x in clean if x < value)
    return round(100.0 * below / len(clean), 1)


def _realized_vol(prices, window=20):
    if not prices or len(prices) < 5:
        return None
    rets = []
    for i in range(1, len(prices)):
        if prices[i - 1] and prices[i] and prices[i - 1] > 0:
            rets.append(math.log(prices[i] / prices[i - 1]))
    if len(rets) < 3:
        return None
    use = rets[-window:] if len(rets) >= window else rets
    mean = sum(use) / len(use)
    var = sum((r - mean) ** 2 for r in use) / max(len(use) - 1, 1)
    std = math.sqrt(var)
    return std * 100


def _trend_strength(prices, window=20):
    if not prices or len(prices) < 5:
        return None, "Unknown"
    use = prices[-window:] if len(prices) >= window else prices
    if use[0] is None or use[-1] is None or use[0] == 0:
        return None, "Unknown"
    net = abs(use[-1] - use[0])
    path = 0.0
    for i in range(1, len(use)):
        if use[i] is not None and use[i - 1] is not None:
            path += abs(use[i] - use[i - 1])
    efficiency = (net / path) if path > 0 else 0.0
    slope = (use[-1] - use[0]) / use[0] * 100
    strength = min(100.0, efficiency * 120)
    if slope > 1.5 and strength >= 25:
        label = "Uptrend"
    elif slope < -1.5 and strength >= 25:
        label = "Downtrend"
    elif strength < 20:
        label = "Range / Chop"
    else:
        label = "Mild trend"
    return round(strength, 1), label


def compute_regime(history, current_price=None):
    prices = [h.get("price") for h in history if h.get("price") is not None]
    if current_price is not None:
        prices = prices + [current_price]
    if len(prices) < 8:
        return {
            "vol_percentile": None,
            "vol_label": "Unknown",
            "trend_strength": None,
            "trend_label": "Unknown",
            "regime": "Unknown",
            "notes": ["Insufficient price history for regime"],
        }
    vol_series = []
    for i in range(10, len(prices) + 1):
        v = _realized_vol(prices[:i], window=14)
        if v is not None:
            vol_series.append(v)
    current_vol = _realized_vol(prices, window=14)
    vol_pct = _percentile_rank(current_vol, vol_series) if vol_series else None
    if vol_pct is None:
        vol_label = "Unknown"
    elif vol_pct >= 75:
        vol_label = "High"
    elif vol_pct <= 25:
        vol_label = "Low"
    else:
        vol_label = "Normal"
    trend_str, trend_label = _trend_strength(prices, window=20)
    if vol_label == "High" and trend_label in ("Uptrend", "Downtrend"):
        regime = "HighVolTrend"
    elif vol_label == "High":
        regime = "HighVolChop"
    elif vol_label == "Low" and trend_label in ("Uptrend", "Downtrend", "Mild trend"):
        regime = "LowVolTrend"
    elif trend_label == "Range / Chop":
        regime = "Range"
    else:
        regime = "NormalTrend"
    notes = []
    if vol_label == "High":
        notes.append("Elevated volatility — moves can reverse quickly")
    if vol_label == "Low":
        notes.append("Low volatility — breakouts may have more follow-through once they occur")
    if trend_label == "Range / Chop":
        notes.append("No clear directional efficiency; mean-reversion risk higher")
    return {
        "vol_percentile": vol_pct,
        "vol_label": vol_label,
        "trend_strength": trend_str,
        "trend_label": trend_label,
        "regime": regime,
        "notes": notes,
        "current_vol": round(current_vol, 3) if current_vol is not None else None,
    }


def compute_positioning(history, current_funding=None, current_oi=None, current_price=None):
    fundings = [h.get("funding_rate") for h in history if h.get("funding_rate") is not None]
    ois = [h.get("open_interest") for h in history if h.get("open_interest") is not None]
    prices = [h.get("price") for h in history if h.get("price") is not None]
    if current_funding is not None:
        fundings = fundings + [current_funding]
    if current_oi is not None:
        ois = ois + [current_oi]
    if current_price is not None:
        prices = prices + [current_price]
    fund_pct = _percentile_rank(current_funding, fundings) if current_funding is not None else None
    if fund_pct is None:
        fund_extreme = "Unknown"
    elif fund_pct >= 90:
        fund_extreme = "Extremely Positive (crowded long)"
    elif fund_pct >= 75:
        fund_extreme = "Strongly Positive"
    elif fund_pct <= 10:
        fund_extreme = "Extremely Negative (crowded short)"
    elif fund_pct <= 25:
        fund_extreme = "Strongly Negative"
    else:
        fund_extreme = "Neutral range"
    oi_chg = None
    if len(ois) >= 6:
        oi_chg = _safe_pct(ois[-1], ois[-6])
    elif len(ois) >= 2:
        oi_chg = _safe_pct(ois[-1], ois[0])
    oi_chgs = []
    for i in range(5, len(ois)):
        c = _safe_pct(ois[i], ois[i - 5])
        if c is not None:
            oi_chgs.append(c)
    oi_chg_pctile = _percentile_rank(oi_chg, oi_chgs) if oi_chg is not None and oi_chgs else None
    crowded_long = False
    crowded_short = False
    reasons = []
    if fund_pct is not None and fund_pct >= 80 and oi_chg is not None and oi_chg > 2:
        crowded_long = True
        reasons.append(f"Funding in top {100 - fund_pct:.0f}% and OI rising ({oi_chg:+.1f}%) → crowded long risk")
    elif fund_pct is not None and fund_pct >= 85:
        crowded_long = True
        reasons.append(f"Funding extremely elevated (percentile {fund_pct}) → longs crowded")
    if fund_pct is not None and fund_pct <= 20 and oi_chg is not None and oi_chg > 2:
        crowded_short = True
        reasons.append(f"Funding in bottom {fund_pct:.0f}% and OI rising → crowded short / short-squeeze potential")
    elif fund_pct is not None and fund_pct <= 15:
        crowded_short = True
        reasons.append(f"Funding extremely negative (percentile {fund_pct}) → shorts crowded")
    price_chg_recent = None
    if len(prices) >= 4:
        price_chg_recent = _safe_pct(prices[-1], prices[-4])
        if price_chg_recent is not None and abs(price_chg_recent) < 0.8 and fund_pct and fund_pct >= 80:
            reasons.append("Price stalling while funding elevated → possible exhaustion")
    pressure = 0
    if fund_pct is not None:
        pressure += min(40, abs(fund_pct - 50) * 0.7)
    if oi_chg is not None and abs(oi_chg) > 3:
        pressure += min(25, abs(oi_chg) * 2)
    if crowded_long or crowded_short:
        pressure += 20
    if price_chg_recent is not None and abs(price_chg_recent) < 0.8 and (crowded_long or crowded_short):
        pressure += 15
    pressure = min(100, int(round(pressure)))
    squeeze_risk = "High" if crowded_short else "Moderate" if fund_pct is not None and fund_pct <= 25 else "Low"
    return {
        "funding_percentile": fund_pct,
        "funding_extreme": fund_extreme,
        "oi_change_pct_recent": round(oi_chg, 2) if oi_chg is not None else None,
        "oi_change_percentile": oi_chg_pctile,
        "crowded_long": crowded_long,
        "crowded_short": crowded_short,
        "squeeze_risk": squeeze_risk,
        "positioning_pressure": pressure,
        "reasons": reasons,
    }


def adjust_sudden_drop_for_positioning(base_risk_level, positioning, regime, calibration=None):
    order = ["Low", "Moderate", "High", "Very High"]
    try:
        idx = order.index(base_risk_level)
    except ValueError:
        idx = 0
    extra = []
    if positioning.get("crowded_long"):
        idx = min(3, idx + 1)
        extra.append("Crowded-long positioning elevates sudden-drop risk")
    weights = (calibration or {}).get("weights", {})
    pressure_weight = float(weights.get("positioning_context", 1.0) or 1.0)
    pressure_threshold = max(60.0, min(85.0, 70.0 / pressure_weight))
    # High pressure is directional downside evidence only when longs are crowded.
    # Crowded shorts instead create squeeze/reversal risk, recorded separately.
    if positioning.get("crowded_long") and positioning.get("positioning_pressure", 0) >= pressure_threshold:
        idx = min(3, idx + 1)
        extra.append(f"High long-side positioning pressure (threshold {pressure_threshold:.0f})")
    if regime.get("regime") == "HighVolChop":
        idx = min(3, idx + 1)
        extra.append("High-vol chop regime increases reversal risk")
    if regime.get("regime") == "LowVolTrend" and not positioning.get("crowded_long"):
        idx = max(0, idx - 1)
    return order[idx], extra
