"""Run the v2 intelligence pipeline in isolated shadow mode.

Usage:
    python -m intelligence_v2.shadow_runner --output /tmp/shadow.json
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone

from .challenge import evaluate as evaluate_challenge
from .comparator import compare
from .contracts import ShadowRun, to_dict
from .decision import synthesize
from .evidence import build_all
from .risk import evaluate as evaluate_risk
from .technical import evaluate

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "ZAMAUSDT"]


def run(history_dir: str, symbols: list[str], timeframe: str) -> ShadowRun:
    started = datetime.now(timezone.utc)
    run_id = f"shadow-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    packs = build_all(history_dir, symbols, timeframe, run_id)
    btc_history = []
    try:
        with open(os.path.join(history_dir, "BTCUSDT.json"), "r", encoding="utf-8") as handle:
            btc_history = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    outputs = [evaluate(pack, history_dir, btc_history) for pack in packs]
    risks = []
    challenges = []
    comparisons = []
    decisions = []
    for pack, technical in zip(packs, outputs):
        risk = evaluate_risk(pack, technical)
        risks.append(risk)
        challenge = evaluate_challenge(pack, technical, risk)
        challenges.append(challenge)
        comparison = compare(
            pack,
            technical,
            risk,
            challenge,
            os.path.join(history_dir, "mechanism_history.json"),
        )
        comparisons.append(comparison)
        decisions.append(synthesize(pack, technical, risk, challenge, comparison))
    completed = datetime.now(timezone.utc)
    return ShadowRun(
        run_id=run_id,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        timeframe=timeframe,
        mode="shadow-read-only",
        evidence_packs=packs,
        agent_outputs=outputs,
        risk_assessments=risks,
        challenge_reports=challenges,
        comparisons=comparisons,
        decisions=decisions,
        safety={
            "legacy_system_touched": False,
            "telegram_sent": False,
            "orders_submitted": False,
            "legacy_history_written": False,
            "promotion_required": True,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated v2 crypto intelligence shadow mode")
    parser.add_argument("--history-dir", default="history")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--output", default="shadow_artifacts/latest.json")
    args = parser.parse_args()
    result = run(args.history_dir, [x.strip() for x in args.symbols.split(",") if x.strip()], args.timeframe)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(to_dict(result), handle, indent=2, ensure_ascii=False)
    print(json.dumps({
        "run_id": result.run_id,
        "mode": result.mode,
        "symbols": len(result.agent_outputs),
        "safety": result.safety,
        "output": args.output,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
