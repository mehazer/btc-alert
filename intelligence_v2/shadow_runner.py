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

from . import coin_agent, interpreter, portfolio_agent, promotion, venues
from .challenge import evaluate as evaluate_challenge
from .comparator import compare
from .contracts import ForecastScorecard, ShadowRun, to_dict
from .decision import synthesize
from .evidence import build_all
from .evaluation import build_scorecard
from . import forecast as forecast_engine
from . import forecast_eval
from .ledger import process_shadow_run
from .risk import evaluate as evaluate_risk
from .technical import evaluate

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "ZAMAUSDT"]


def run(history_dir: str, symbols: list[str], timeframe: str, persist_ledger: bool = True):
    started = datetime.now(timezone.utc)
    run_id = f"shadow-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    packs = build_all(history_dir, symbols, timeframe, run_id)

    venue_snapshot = {}
    try:
        venue_snapshot = venues.snapshot_symbols(symbols)
        venues.save_snapshot(history_dir, venue_snapshot)
    except Exception as exc:
        venue_snapshot = {"status": "error", "detail": str(exc)}

    btc_history = []
    try:
        with open(os.path.join(history_dir, "BTCUSDT.json"), "r", encoding="utf-8") as handle:
            btc_history = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    btc_change = None
    if len(btc_history) >= 2 and btc_history[-1].get("price") and btc_history[-2].get("price"):
        btc_change = (btc_history[-1]["price"] - btc_history[-2]["price"]) / btc_history[-2]["price"] * 100

    outputs = [evaluate(pack, history_dir, btc_history) for pack in packs]
    risks = []
    challenges = []
    comparisons = []
    decisions = []
    interpretations = []
    coin_theses = []
    forecasts = []
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
        decision = synthesize(pack, technical, risk, challenge, comparison)
        decisions.append(decision)
        forecasts.append(forecast_engine.issue(pack, technical, risk, decision))
        interpretations.append(
            interpreter.interpret(pack, technical, risk, challenge, comparison, decision)
        )
        coin_theses.append(
            coin_agent.evaluate(pack, technical, risk, btc_change_pct=btc_change)
        )

    portfolio = portfolio_agent.evaluate(
        run_id,
        decisions,
        portfolio_path=os.path.join(history_dir, "intelligence_v2", "paper_portfolio.json"),
    )
    scorecard = build_scorecard(decisions, risks, history_dir)
    evaluations = scorecard.records
    forecast_summary = forecast_eval.process_forecasts(history_dir, forecasts) if persist_ledger else {}
    forecast_scorecard = None
    if forecast_summary.get("settle", {}).get("scorecard"):
        sc = forecast_summary["settle"]["scorecard"]
        forecast_scorecard = ForecastScorecard(**{k: sc[k] for k in ForecastScorecard.__dataclass_fields__ if k in sc})
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
        forecasts=forecasts,
        forecast_evaluations=[],
        forecast_scorecard=forecast_scorecard,
        evaluations=evaluations,
        scorecard=scorecard,
        interpretations=interpretations,
        coin_theses=coin_theses,
        portfolio=portfolio,
        promotion=None,
        safety={
            "legacy_system_touched": False,
            "telegram_sent": False,
            "orders_submitted": False,
            "legacy_history_written": False,
            "v2_ledger_written": bool(persist_ledger),
            "promotion_required": True,
            "auto_promote": False,
            "phase": 10,
            "venue_snapshot_saved": bool(venue_snapshot) and venue_snapshot.get("status") != "error",
            "forecast_engine": True,
        },
    )
    ledger_summary = {}
    if persist_ledger:
        ledger_summary = process_shadow_run(history_dir, shadow)
        ledger_summary["forecast"] = forecast_summary
    promo = promotion.evaluate(run_id, history_dir=history_dir)
    shadow = ShadowRun(
        run_id=shadow.run_id,
        started_at=shadow.started_at,
        completed_at=shadow.completed_at,
        timeframe=shadow.timeframe,
        mode=shadow.mode,
        evidence_packs=shadow.evidence_packs,
        agent_outputs=shadow.agent_outputs,
        risk_assessments=shadow.risk_assessments,
        challenge_reports=shadow.challenge_reports,
        comparisons=shadow.comparisons,
        decisions=shadow.decisions,
        forecasts=shadow.forecasts,
        forecast_evaluations=shadow.forecast_evaluations,
        forecast_scorecard=shadow.forecast_scorecard,
        evaluations=shadow.evaluations,
        scorecard=shadow.scorecard,
        interpretations=shadow.interpretations,
        coin_theses=shadow.coin_theses,
        portfolio=shadow.portfolio,
        promotion=promo,
        safety=shadow.safety,
    )
    if persist_ledger:
        promo_path = os.path.join(history_dir, "intelligence_v2", "promotion_report.json")
        os.makedirs(os.path.dirname(promo_path), exist_ok=True)
        with open(promo_path, "w", encoding="utf-8") as handle:
            json.dump(to_dict(promo), handle, indent=2, ensure_ascii=False)
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
        "forecasts": len(result.forecasts),
        "forecast_scorecard": to_dict(result.forecast_scorecard) if result.forecast_scorecard else None,
        "safety": result.safety,
        "promotion": to_dict(result.promotion) if result.promotion else None,
        "portfolio_warnings": (result.portfolio.warnings if result.portfolio else []),
        "ledger": ledger_summary,
        "output": args.output,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
