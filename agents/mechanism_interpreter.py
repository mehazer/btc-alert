"""Deterministic mechanism interpreter used by v2 technical adapter and alerts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return ((a - b) / b) * 100


def _dir_label(change_pct, flat_band=0.3):
    if change_pct is None:
        return "Unknown"
    if change_pct > flat_band:
        return "Rising"
    if change_pct < -flat_band:
        return "Falling"
    return "Flat"


def analyze(
    symbol: str,
    history: List[dict],
    current_price: Optional[float],
    current_oi: Optional[float],
    current_volume: Optional[float],
    current_funding: Optional[float],
    current_rsi: Optional[float] = None,
    btc_history: Optional[List[dict]] = None,
    btc_price_change_pct: Optional[float] = None,
    timeframe: str = "4h",
) -> Dict[str, Any]:
    evaluated_at = datetime.now(timezone.utc).isoformat()
    if not history or current_price is None:
        return {
            "status": "error",
            "symbol": symbol,
            "timeframe": timeframe,
            "evaluated_at": evaluated_at,
            "missing": ["price" if current_price is None else "history"],
            "likely_mechanism": {"primary": "Low conviction / wait", "secondary": None, "reasoning": ["Insufficient data"]},
            "sudden_drop_risk": {"level": "Unknown", "score": 0, "reasons": []},
            "current_relationship": {},
            "market_summary": {},
            "confirmation_signals": {"bullish": [], "warnings": []},
        }

    prev = history[-2] if len(history) >= 2 else history[-1]
    price_chg = _pct(current_price, prev.get("price"))
    prev_oi = prev.get("oi") or prev.get("open_interest")
    oi_chg = _pct(current_oi, prev_oi) if current_oi is not None and prev_oi not in (None, 0) else None

    vols = [h.get("volume") for h in history[-20:] if h.get("volume") is not None]
    avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1) if len(vols) > 1 else (vols[0] if vols else None)
    vol_ratio = (current_volume / avg_vol) if current_volume and avg_vol else None
    high_vol = vol_ratio is not None and vol_ratio >= 1.35

    price_dir = _dir_label(price_chg)
    oi_dir = _dir_label(oi_chg)

    primary = "Low conviction / wait"
    secondary = None
    reasons: List[str] = []

    if price_dir == "Rising":
        if oi_dir == "Rising" and oi_chg is not None and abs(oi_chg) >= 5:
            primary = "Fresh long buildup"
            secondary = "Leverage crowding risk"
            reasons.append("Price up with sharp OI expansion — leveraged participation.")
        elif oi_dir == "Rising":
            primary = "Fresh long buildup"
            reasons.append("Price up with OI expansion — new longs likely entering.")
        elif oi_dir == "Falling" and high_vol:
            primary = "Short covering"
            secondary = "Weak rally risk"
            reasons.append("Price up while OI contracts on volume — short covering footprint.")
        elif oi_dir == "Falling":
            primary = "Short covering"
            reasons.append("Price up while OI contracts — covering more likely than fresh longs.")
        elif high_vol:
            primary = "Spot-led buying"
            reasons.append("Price up on volume with limited OI signal — spot-led or mixed.")
        else:
            primary = "Weakening participation"
            reasons.append("Price up but participation signals are soft.")
    elif price_dir == "Falling":
        if oi_dir == "Falling" and high_vol:
            primary = "Long liquidation"
            secondary = "Deleveraging"
            reasons.append("Price down with OI contraction on volume — long unwinding.")
        elif oi_dir == "Rising":
            primary = "Fresh short buildup"
            reasons.append("Price down with OI expansion — fresh short positioning.")
        else:
            primary = "Soft selling / drift"
            reasons.append("Price down without strong OI confirmation.")
    else:
        reasons.append("Price effectively flat; no clear directional mechanism.")

    risk_score = 0
    risk_reasons = []
    if price_dir == "Rising" and oi_chg is not None and oi_chg >= 5:
        risk_score += 2
        risk_reasons.append("Sharp OI expansion into the rise")
    if current_funding is not None and current_funding >= 0.0003:
        risk_score += 2
        risk_reasons.append("Strongly positive funding (crowded longs)")
    elif current_funding is not None and current_funding > 0.00005:
        risk_score += 1
        risk_reasons.append("Positive funding")
    if vol_ratio is not None and vol_ratio >= 2.0:
        risk_score += 1
        risk_reasons.append("Climactic volume")
    if primary == "Weakening participation":
        risk_score += 1
        risk_reasons.append("Weakening participation on the rise")

    if risk_score >= 5:
        risk_level = "Very High"
    elif risk_score >= 3:
        risk_level = "High"
    elif risk_score >= 2:
        risk_level = "Moderate"
    else:
        risk_level = "Low"

    return {
        "status": "ok",
        "symbol": symbol,
        "timeframe": timeframe,
        "evaluated_at": evaluated_at,
        "likely_mechanism": {
            "primary": primary,
            "secondary": secondary,
            "reasoning": reasons or [f"Classified as {primary}"],
        },
        "sudden_drop_risk": {
            "level": risk_level,
            "score": risk_score,
            "reasons": risk_reasons,
        },
        "current_relationship": {
            "price": f"Price: {price_dir} — {price_chg:+.2f}%" if price_chg is not None else "Price: n/a",
            "oi": f"OI: {oi_dir} — {oi_chg:+.2f}%" if oi_chg is not None else "OI: n/a",
            "volume": f"Volume ratio: {vol_ratio:.2f}x" if vol_ratio is not None else "Volume: n/a",
            "funding": f"Funding: {current_funding}" if current_funding is not None else "Funding: n/a",
        },
        "market_summary": {
            "price": current_price,
            "oi": current_oi,
            "volume": current_volume,
            "funding": current_funding,
            "rsi": current_rsi,
        },
        "confirmation_signals": {
            "bullish": [r for r in reasons if "long" in r.lower() or "buying" in r.lower()],
            "warnings": risk_reasons[:2],
        },
    }


def format_telegram(analysis: Dict[str, Any]) -> str:
    symbol = analysis.get("symbol") or "?"
    timeframe = analysis.get("timeframe") or "?"
    evaluated = (analysis.get("evaluated_at") or "")[:16].replace("T", " ")
    if analysis.get("status") != "ok":
        return f"{symbol} · {timeframe}\nRead: Data incomplete\nStance: Wait"
    m = analysis.get("likely_mechanism") or {}
    risk = analysis.get("sudden_drop_risk") or {}
    primary = m.get("primary") or "Low conviction / wait"
    lines = [
        f"{symbol} · {timeframe} · {evaluated} UTC",
        f"Read: {primary}",
        f"Drop-risk: {risk.get('level', 'Unknown')}",
        "",
        "Note: Mechanism read, not a trade signal.",
    ]
    return "\n".join(lines)
