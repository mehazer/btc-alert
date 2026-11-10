"""Read-only adapter from legacy JSON history to canonical v2 evidence."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from .contracts import EvidencePack, Observation, QualityGate, SourceRef


def _read(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(x) for x in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def _source_refs(row: dict, symbol: str) -> List[SourceRef]:
    observed = row.get("date") or datetime.now(timezone.utc).isoformat()
    retrieved = row.get("retrieved_at") or observed
    return [
        SourceRef(
            source_id=_id("src", symbol, observed, "binance-spot"),
            venue="Binance",
            dataset="spot-candle",
            observed_at=str(observed),
            retrieved_at=str(retrieved),
            unit="quote price / base volume",
            reliability="legacy-record",
        ),
        SourceRef(
            source_id=_id("src", symbol, observed, "okx-perpetual"),
            venue="OKX",
            dataset="perpetual-OI-funding",
            observed_at=str(observed),
            retrieved_at=str(retrieved),
            unit="coin OI / funding rate",
            reliability="legacy-record",
        ),
    ]


def _normalize_row(row: dict) -> dict:
    """Map alert_bot 4h fields onto the rich history schema."""
    out = dict(row)
    if out.get("open_interest") is None and out.get("oi") is not None:
        out["open_interest"] = out["oi"]
    if out.get("funding_rate") is None and out.get("funding") is not None:
        out["funding_rate"] = out["funding"]
    if out.get("date") is None and out.get("ts") is not None:
        try:
            ts = int(float(out["ts"]))
            if ts > 10_000_000_000:
                out["date"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
            else:
                out["date"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            pass
    return out


def _load_symbol_rows(history_dir: str, symbol: str) -> List[dict]:
    """Prefer rich SYMBOL.json; merge tip OI from SYMBOL_4h.json when needed."""
    rich = _read(os.path.join(history_dir, f"{symbol}.json"), [])
    rich = [_normalize_row(r) for r in rich] if isinstance(rich, list) else []
    alt = _read(os.path.join(history_dir, f"{symbol}_4h.json"), [])
    alt = [_normalize_row(r) for r in alt] if isinstance(alt, list) else []

    if not rich and alt:
        return alt
    if not alt:
        return rich

    if rich and alt:
        rt, at = rich[-1], alt[-1]
        if rt.get("open_interest") is None and at.get("open_interest") is not None:
            rt["open_interest"] = at.get("open_interest")
            rt["oi"] = at.get("open_interest")
            rt["funding_rate"] = at.get("funding_rate") if at.get("funding_rate") is not None else at.get("funding")
            if len(rich) >= 2 and rich[-2].get("open_interest") is None:
                if len(alt) >= 2 and alt[-2].get("open_interest") is not None:
                    rich[-2]["open_interest"] = alt[-2]["open_interest"]
                    rich[-2]["oi"] = alt[-2]["open_interest"]
    return rich


def _quality(rows: List[dict], row: dict, timeframe: str) -> List[QualityGate]:
    checks = []
    checks.append(QualityGate("history_depth", "pass" if len(rows) >= 20 else "warn", f"{len(rows)} observations available"))
    required = ["price", "volume", "date"]
    missing = [key for key in required if row.get(key) is None]
    checks.append(QualityGate("spot_fields", "fail" if missing else "pass", f"missing={missing or 'none'}"))
    derivatives_missing = [key for key in ["open_interest", "funding_rate"] if row.get(key) is None]
    checks.append(QualityGate("derivatives_fields", "warn" if derivatives_missing else "pass", f"missing={derivatives_missing or 'none'}"))
    oi_recent = [r.get("open_interest") for r in rows[-12:] if isinstance(r, dict)]
    oi_filled = sum(1 for v in oi_recent if v is not None)
    oi_ok = oi_filled >= 3 and row.get("open_interest") is not None
    checks.append(QualityGate(
        "oi_history_ok",
        "pass" if oi_ok else "warn",
        f"oi_filled_last_12={oi_filled}; current_oi={'yes' if row.get('open_interest') is not None else 'no'}",
    ))
    checks.append(QualityGate(
        "source_provenance",
        "pass" if row.get("retrieved_at") or row.get("date") else "warn",
        f"retrieved_at={'present' if row.get('retrieved_at') else 'fallback-to-observed'}",
    ))
    checks.append(QualityGate(
        "derivatives_units",
        "pass",
        f"oi_unit={row.get('oi_unit') or 'legacy-unspecified'}, funding_unit={row.get('funding_unit') or 'legacy-unspecified'}; non-blocking until raw unit metadata is backfilled",
    ))
    checks.append(QualityGate("timeframe_declared", "pass" if timeframe else "fail", timeframe or "missing timeframe"))
    return checks


def _row_oi_change(row: dict, rows: List[dict]) -> Optional[float]:
    if row.get("oi_change_pct") is not None:
        try:
            return float(row["oi_change_pct"])
        except (TypeError, ValueError):
            pass
    current = _number(row.get("open_interest") if row.get("open_interest") is not None else row.get("oi"))
    prior = None
    for prev in reversed(rows[:-1]):
        prior = _number(prev.get("open_interest") if prev.get("open_interest") is not None else prev.get("oi"))
        if prior not in (None, 0):
            break
    if current is None or prior in (None, 0):
        return None
    return (current - prior) / prior * 100


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _returns(rows: List[dict]) -> List[float]:
    values = [_number(row.get("price")) for row in rows]
    output: List[float] = []
    for previous, current in zip(values, values[1:]):
        if previous and current and previous > 0 and current > 0:
            output.append(math.log(current / previous))
        else:
            output.append(float("nan"))
    return output


def _clean(values: Iterable[Optional[float]]) -> List[float]:
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def _ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    current = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for value in values[period:]:
        current = alpha * value + (1 - alpha) * current
    return current


def _percentile_rank(value: Optional[float], history: List[float]) -> Optional[float]:
    if value is None or not history:
        return None
    less_or_equal = sum(1 for item in history if item <= value)
    return round(100 * less_or_equal / len(history), 2)


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in (None, 0):
        return None
    return round((current / previous - 1) * 100, 6)


def _compact_features(rows: List[dict], btc_rows: Optional[List[dict]] = None) -> Dict[str, Any]:
    prices = _clean(_number(row.get("price")) for row in rows)
    volumes = _clean(_number(row.get("volume")) for row in rows)
    oi_values = [_number(row.get("open_interest")) for row in rows]
    funding_values = [_number(row.get("funding_rate")) for row in rows]
    log_returns = _clean(_returns(rows))
    current_price = prices[-1] if prices else None
    ema50 = _ema(prices, 50)
    ema200 = _ema(prices, 200)
    return_12 = log_returns[-12:] if log_returns else []
    downside = [value for value in return_12 if value < 0]
    prior_volumes = volumes[-21:-1]
    current_volume = volumes[-1] if volumes else None
    current_oi = oi_values[-1] if oi_values else None
    prior_oi = next((v for v in reversed(oi_values[:-1]) if v not in (None, 0)), None)
    current_funding = funding_values[-1] if funding_values else None
    funding_history = _clean(funding_values[-31:-1])
    oi_history = _clean(oi_values[-31:-1])
    current_oi_change = _pct_change(current_oi, prior_oi)
    price_change_3 = _pct_change(prices[-1], prices[-4]) if len(prices) >= 4 else None
    price_change_6 = _pct_change(prices[-1], prices[-7]) if len(prices) >= 7 else None
    btc_relative_3 = None
    btc_relative_6 = None
    if btc_rows and len(btc_rows) >= 4 and len(rows) >= 4:
        btc_relative_3 = round(((_number(rows[-1].get("price")) or 0) / (_number(rows[-4].get("price")) or 1) - 1) * 100 - ((_number(btc_rows[-1].get("price")) or 0) / (_number(btc_rows[-4].get("price")) or 1) - 1) * 100, 6)
    if btc_rows and len(btc_rows) >= 7 and len(rows) >= 7:
        btc_relative_6 = round(((_number(rows[-1].get("price")) or 0) / (_number(rows[-7].get("price")) or 1) - 1) * 100 - ((_number(btc_rows[-1].get("price")) or 0) / (_number(btc_rows[-7].get("price")) or 1) - 1) * 100, 6)
    if price_change_3 is None or current_oi_change is None:
        quadrant = "unavailable"
    elif price_change_3 > 0 and current_oi_change > 0:
        quadrant = "price_up_oi_up"
    elif price_change_3 > 0 and current_oi_change <= 0:
        quadrant = "price_up_oi_down"
    elif price_change_3 <= 0 and current_oi_change > 0:
        quadrant = "price_down_oi_up"
    else:
        quadrant = "price_down_oi_down"
    return {
        "feature_version": "compact-v1",
        "trend": {
            "ema50_distance_pct": round((current_price / ema50 - 1) * 100, 6) if current_price and ema50 else None,
            "ema50_slope_6_pct": _pct_change(_ema(prices[:-6], 50), _ema(prices[:-12], 50)) if len(prices) >= 62 else None,
            "above_ema200": current_price > ema200 if current_price and ema200 else None,
        },
        "momentum": {
            "rsi": _number(rows[-1].get("rsi")) if rows else None,
            "price_change_3_pct": price_change_3,
            "price_change_6_pct": price_change_6,
        },
        "volatility": {
            "realized_vol_12": round(math.sqrt(sum(value * value for value in return_12) / len(return_12)), 8) if return_12 else None,
            "downside_vol_12": round(math.sqrt(sum(value * value for value in downside) / len(downside)), 8) if downside else None,
            "realized_vol_percentile_90": _percentile_rank(
                math.sqrt(sum(value * value for value in return_12) / len(return_12)) if return_12 else None,
                [math.sqrt(sum(window * window for window in log_returns[index - 12:index]) / 12) for index in range(12, len(log_returns) + 1) if len(log_returns[index - 12:index]) == 12][-90:],
            ),
        },
        "participation": {
            "relative_volume": round(current_volume / median(prior_volumes), 6) if current_volume and prior_volumes and median(prior_volumes) else None,
        },
        "derivatives": {
            "oi_change_3_pct": current_oi_change,
            "oi_percentile_30": _percentile_rank(current_oi, oi_history),
            "funding_rate": current_funding,
            "funding_percentile_30": _percentile_rank(current_funding, funding_history),
            "price_oi_quadrant": quadrant,
        },
        "relative_strength": {
            "vs_btc_3_pct": btc_relative_3,
            "vs_btc_6_pct": btc_relative_6,
        },
    }


def build_evidence_pack(history_dir: str, symbol: str, timeframe: str, run_id: str) -> EvidencePack:
    rows = _load_symbol_rows(history_dir, symbol)
    row = rows[-1] if rows else {}
    previous = rows[-2] if len(rows) > 1 else {}
    btc_rows = _load_symbol_rows(history_dir, "BTCUSDT") if symbol != "BTCUSDT" else rows
    observed = str(row.get("date") or datetime.now(timezone.utc).isoformat())
    observation = Observation(
        observation_id=_id("obs", symbol, observed, timeframe),
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed,
        price=row.get("price"),
        volume=row.get("volume"),
        open_interest=row.get("open_interest"),
        funding_rate=row.get("funding_rate"),
        rsi=row.get("rsi"),
        price_change_pct=row.get("price_change_pct"),
        oi_change_pct=_row_oi_change(row, rows),
        source_refs=_source_refs(row, symbol),
    )
    price = row.get("price")
    previous_price = previous.get("price")
    price_change = ((price - previous_price) / previous_price * 100) if price and previous_price else None
    volume = row.get("volume")
    prior_volumes = [r.get("volume") for r in rows[-21:-1] if r.get("volume") is not None]
    avg_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else None
    features = {
        "price_change_pct_recomputed": round(price_change, 6) if price_change is not None else None,
        "volume_ratio": round(volume / avg_volume, 4) if volume is not None and avg_volume else None,
        "direction": "up" if price_change and price_change > 0 else "down" if price_change and price_change < 0 else "flat",
        "source_venues": ["Binance spot", "OKX perpetual"],
        "compact_indicators": _compact_features(rows, btc_rows if symbol != "BTCUSDT" else None),
    }
    limitations = [
        "Legacy JSON is the source for this shadow run; raw exchange payloads are not yet persisted here.",
        "OI is venue-specific and does not reveal long/short direction by itself.",
        "Liquidations and derivatives volume may be unavailable.",
        "OI series fills forward as pipeline runs persist open_interest on each bar.",
    ]
    evidence_id = _id("evidence", run_id, symbol, observed, timeframe)
    return EvidencePack(
        evidence_id=evidence_id,
        run_id=run_id,
        symbol=symbol,
        timeframe=timeframe,
        created_at=datetime.now(timezone.utc).isoformat(),
        observations=[observation],
        quality_gates=_quality(rows, row, timeframe),
        features=features,
        limitations=limitations,
    )


def build_all(history_dir: str, symbols: Iterable[str], timeframe: str, run_id: str) -> List[EvidencePack]:
    return [build_evidence_pack(history_dir, symbol, timeframe, run_id) for symbol in symbols]
