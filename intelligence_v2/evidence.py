"""Read-only adapter from legacy JSON history to canonical v2 evidence."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List

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


def _quality(rows: List[dict], row: dict, timeframe: str) -> List[QualityGate]:
    checks = []
    checks.append(QualityGate("history_depth", "pass" if len(rows) >= 20 else "warn", f"{len(rows)} observations available"))
    required = ["price", "volume", "date"]
    missing = [key for key in required if row.get(key) is None]
    checks.append(QualityGate("spot_fields", "fail" if missing else "pass", f"missing={missing or 'none'}"))
    derivatives_missing = [key for key in ["open_interest", "funding_rate"] if row.get(key) is None]
    checks.append(QualityGate("derivatives_fields", "warn" if derivatives_missing else "pass", f"missing={derivatives_missing or 'none'}"))
    checks.append(QualityGate("timeframe_declared", "pass" if timeframe else "fail", timeframe or "missing timeframe"))
    return checks


def build_evidence_pack(history_dir: str, symbol: str, timeframe: str, run_id: str) -> EvidencePack:
    rows = _read(os.path.join(history_dir, f"{symbol}.json"), [])
    rows = rows if isinstance(rows, list) else []
    row = rows[-1] if rows else {}
    previous = rows[-2] if len(rows) > 1 else {}
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
        oi_change_pct=row.get("oi_change_pct"),
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
    }
    limitations = [
        "Legacy JSON is the source for this shadow run; raw exchange payloads are not yet persisted here.",
        "OI is venue-specific and does not reveal long/short direction by itself.",
        "Liquidations and derivatives volume may be unavailable.",
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
