"""Build history/intelligence_v2/dashboard_snapshot.json from the decision ledger.
Browser scorecard must never load the full multi-MB ledger.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

HISTORY = os.environ.get("HISTORY_DIR", "history")
BASE = os.path.join(HISTORY, "intelligence_v2")


def main() -> None:
    path = os.path.join(BASE, "decision_ledger.json")
    meta_path = os.path.join(BASE, "ledger_meta.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            decisions = json.load(handle)
        if not isinstance(decisions, list):
            decisions = []
    except Exception:
        decisions = []
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle) or {}
    except Exception:
        meta = {}

    latest = {}
    pending = []
    for row in decisions:
        latest[row.get("symbol")] = {
            "symbol": row.get("symbol"),
            "decision": row.get("decision"),
            "signal_direction": row.get("signal_direction"),
            "confidence": row.get("confidence"),
            "gate": row.get("gate"),
            "thesis": (row.get("thesis") or "")[:200],
            "evaluated_at": row.get("evaluated_at"),
            "risk_overall_level": row.get("risk_overall_level"),
        }
        for horizon, payload in (row.get("horizons") or {}).items():
            if (payload or {}).get("status") != "settled":
                pending.append(
                    {
                        "symbol": row.get("symbol"),
                        "decision": row.get("decision"),
                        "confidence": row.get("confidence"),
                        "horizon": horizon,
                        "status": (payload or {}).get("status") or "pending",
                        "evaluated_at": row.get("evaluated_at"),
                    }
                )
    pending.sort(key=lambda item: item.get("evaluated_at") or "", reverse=True)
    recent = []
    for row in decisions[-20:]:
        recent.append(
            {
                "symbol": row.get("symbol"),
                "decision": row.get("decision"),
                "signal_direction": row.get("signal_direction"),
                "confidence": row.get("confidence"),
                "gate": row.get("gate"),
                "thesis": (row.get("thesis") or "")[:200],
                "evaluated_at": row.get("evaluated_at"),
                "risk_overall_level": row.get("risk_overall_level"),
            }
        )
    snap = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Lightweight dashboard payload — do not load full decision_ledger.json in the browser",
        "recent_decisions": recent,
        "latest_by_symbol": latest,
        "pending": pending[:100],
        "pending_count": int(meta.get("still_pending") or len(pending)),
        "total_decisions": len(decisions),
        "last_run_id": meta.get("last_run_id"),
    }
    os.makedirs(BASE, exist_ok=True)
    out = os.path.join(BASE, "dashboard_snapshot.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(snap, handle, indent=2, ensure_ascii=False)
    os.replace(tmp, out)
    print(
        f"dashboard_snapshot written pending={snap['pending_count']} shown={len(snap['pending'])}"
    )


if __name__ == "__main__":
    main()
