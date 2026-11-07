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
from .evaluation import build_scorecard
from .ledger import process_shadow_run
from .risk import evaluate as evaluate_risk
from .technical import evaluate

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "ZAMAUSDT"]


def run(history_dir: str, symbols: list[str], timeframe: str, persist_ledger: bool = True):
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
    scorecard = build_scorecard(decisions, risks, history_dir)
    evaluations = scorecard.records
    completed = datetime.now(timezone.utc)
    shadow = ShadowRun(
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
        evaluations=evaluations,
        scorecard=scorecard,
        safety={
            "legacy_system_touched": False,
            "telegram_sent": False,
            "orders_submitted": False,
            "legacy_history_written": False,
            "v2_ledger_written": bool(persist_ledger),
            "promotion_required": True,
        },
    )
    ledger_summary = {}
    if persist_ledger:
        ledger_summary = process_shadow_run(history_dir, shadow)
    return shadow, ledger_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated v2 crypto intelligence shadow mode")
    parser.add_argument("--history-dir", default="history")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--output", default="shadow_artifacts/latest.json")
    parser.add_argument("--no-ledger", action="store_true", help="Skip persistent ledger write")
    args = parser.parse_args()
    result, ledger_summary = run(
        args.history_dir,
        [x.strip() for x in args.symbols.split(",") if x.strip()],
        args.timeframe,
        persist_ledger=not args.no_ledger,
    )
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    payload = to_dict(result)
    payload["ledger"] = ledger_summary
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    print(json.dumps({
        "run_id": result.run_id,
        "mode": result.mode,
        "symbols": len(result.agent_outputs),
        "safety": result.safety,
        "ledger": ledger_summary,
        "output": args.output,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
