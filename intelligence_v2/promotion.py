"""Controlled promotion criteria (Phase 8).

Produces a report only. Never flips production workflows, never sends Telegram,
never sets execution_allowed=true on live systems.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from .contracts import PromotionReport

MIN_SETTLED = 30
MIN_DIRECTION_ACCURACY = 55.0
MIN_TECH_SAMPLES = 15
MIN_RISK_SAMPLES = 10
MAX_FALSE_CALM_PCT = 35.0


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def evaluate(run_id: str, history_dir: str = "history") -> PromotionReport:
    base = os.path.join(history_dir, "intelligence_v2")
    agent_cards = _load(os.path.join(base, "agent_scorecards.json"))
    scorecard = _load(os.path.join(base, "scorecard.json"))
    meta = _load(os.path.join(base, "ledger_meta.json"))

    agents = agent_cards.get("agents") or {}
    tech = agents.get("technical") or {}
    risk = agents.get("risk") or {}
    challenge = agents.get("challenge") or {}

    checks: List[Dict[str, Any]] = []
    blockers: List[str] = []

    settled = int(scorecard.get("settled_records") or 0)
    dir_acc = scorecard.get("direction_accuracy_pct")
    tech_n = int(tech.get("samples") or 0)
    risk_n = int(risk.get("samples") or 0)
    false_calm = risk.get("false_calm_pct")

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "passed": ok, "detail": detail})
        if not ok:
            blockers.append(f"{name}: {detail}")

    add("min_settled_records", settled >= MIN_SETTLED, f"settled={settled}, need>={MIN_SETTLED}")
    add(
        "direction_accuracy",
        dir_acc is not None and float(dir_acc) >= MIN_DIRECTION_ACCURACY,
        f"accuracy={dir_acc}, need>={MIN_DIRECTION_ACCURACY}",
    )
    add("technical_samples", tech_n >= MIN_TECH_SAMPLES, f"technical_samples={tech_n}, need>={MIN_TECH_SAMPLES}")
    add("risk_samples", risk_n >= MIN_RISK_SAMPLES, f"risk_samples={risk_n}, need>={MIN_RISK_SAMPLES}")
    if false_calm is not None:
        add(
            "false_calm_ceiling",
            float(false_calm) <= MAX_FALSE_CALM_PCT,
            f"false_calm_pct={false_calm}, max={MAX_FALSE_CALM_PCT}",
        )
    else:
        add("false_calm_ceiling", False, "false_calm_pct unavailable")

    add("human_approval_required", False, "Promotion requires explicit human PR approval.")
    add("no_auto_execution", True, "execution remains disabled in shadow mode.")

    ready = (
        settled >= MIN_SETTLED
        and dir_acc is not None
        and float(dir_acc) >= MIN_DIRECTION_ACCURACY
        and tech_n >= MIN_TECH_SAMPLES
        and risk_n >= MIN_RISK_SAMPLES
        and (false_calm is None or float(false_calm) <= MAX_FALSE_CALM_PCT)
    )
    status = "ready_for_human_review" if ready else "not_ready"
    if ready:
        blockers = [b for b in blockers if "human_approval" not in b.lower()]
        blockers.append("Metrics gate passed — still requires human PR review before any production change.")

    return PromotionReport(
        agent="promotion-gate",
        run_id=run_id,
        status=status,
        ready_for_review=ready,
        auto_promote=False,
        checks=checks,
        blockers=blockers,
        metrics={
            "settled_records": settled,
            "direction_accuracy_pct": dir_acc,
            "technical_samples": tech_n,
            "risk_samples": risk_n,
            "false_calm_pct": false_calm,
            "challenge_fail_useful_pct": challenge.get("primary_metric_pct"),
            "total_runs": meta.get("total_runs"),
            "total_decisions": meta.get("total_decisions"),
        },
        evaluated_at=datetime.now(timezone.utc).isoformat(),
        model_version="promotion-criteria-v0.1",
    )
