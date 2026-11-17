"""Optional FRED macro series collector (free API key).

Requires FRED_API_KEY in the environment. When the key is missing or a series
fails, status is unavailable — values are never invented.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

FRED_OBS = "https://api.stlouisfed.org/fred/series/observations"

# Compact crypto-relevant macro set.
DEFAULT_SERIES = {
    "DFF": "Fed funds effective rate",
    "DGS10": "10-year Treasury yield",
    "T10Y2Y": "10y-2y Treasury spread",
    "CPIAUCSL": "CPI all urban consumers",
    "VIXCLS": "VIX",
}


def _latest_observation(series_id: str, api_key: str) -> dict[str, Any]:
    response = requests.get(
        FRED_OBS,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        },
        timeout=20,
    )
    response.raise_for_status()
    observations = response.json().get("observations") or []
    for row in observations:
        value = row.get("value")
        if value in (None, ".", ""):
            continue
        return {
            "series_id": series_id,
            "date": row.get("date"),
            "value": float(value),
            "realtime_start": row.get("realtime_start"),
            "realtime_end": row.get("realtime_end"),
        }
    raise RuntimeError(f"No numeric observation for {series_id}")


def collect(series_map: dict[str, str] | None = None) -> dict[str, Any]:
    api_key = os.environ.get("FRED_API_KEY") or os.environ.get("FRED_KEY")
    series_map = series_map or DEFAULT_SERIES
    payload: dict[str, Any] = {
        "status": "ok",
        "source": "FRED",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "series": {},
        "limitations": [],
    }
    if not api_key:
        payload["status"] = "unavailable"
        payload["limitations"].append(
            "FRED_API_KEY is not configured; macro series were not fetched."
        )
        return payload

    failures = 0
    for series_id, label in series_map.items():
        try:
            obs = _latest_observation(series_id, api_key)
            payload["series"][series_id] = {
                "label": label,
                **obs,
            }
        except Exception as exc:
            failures += 1
            payload["limitations"].append(
                f"{series_id} unavailable: {type(exc).__name__}: {exc}"
            )

    if not payload["series"]:
        payload["status"] = "unavailable"
    elif failures:
        payload["status"] = "partial"
    return payload


if __name__ == "__main__":
    import json

    print(json.dumps(collect(), indent=2, ensure_ascii=False))
