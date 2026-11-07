# Intelligence v2 migration and rollback plan

## Current state

The legacy system remains the production system. Its scheduled workflows, Telegram notifications, dashboard, history writers, and self-improvement loop are unchanged by the v2 shadow implementation.

The v2 code is isolated under `intelligence_v2/`. Shadow mode reads existing `history/` files, appends only to `history/intelligence_v2/`, and does not send Telegram messages, rewrite legacy history, access exchange credentials, or submit orders.

## How to run v2 safely

From GitHub Actions, select **Intelligence v2 Shadow** and choose the timeframe and symbols. The output is uploaded as an artifact named `intelligence-v2-shadow-<run_id>`. The persistent ledger is also committed under `history/intelligence_v2/`.

Locally:

```bash
python -m intelligence_v2.shadow_runner \
  --timeframe 4h \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,ZECUSDT,ZAMAUSDT \
  --output /tmp/intelligence-v2-shadow.json
```

## Rollback

The legacy production path is preserved in the `main` branch. To disable v2, do not run the manual shadow workflow. No scheduled production workflow imports `intelligence_v2` for alerts.

If v2 is later promoted, promotion must happen in a separate pull request. The pull request must show:

1. Legacy workflow tests still pass.
2. Shadow and legacy outputs are compared on the same evidence packs.
3. No Telegram or trading side effects occur during comparison.
4. Agent-specific accuracy and calibration results improve or remain within agreed limits.
5. Human approval is recorded before changing the production workflow.

## Phase 1 implemented

- Versioned dataclasses for observations, evidence packs, quality gates, scenarios, agent outputs, and shadow runs.
- Read-only adapter from legacy JSON history to canonical evidence packs.
- Technical Agent adapter that translates existing deterministic calculations into the v2 contract.
- Safety metadata proving legacy history and Telegram were untouched.
- Manual-only GitHub Actions workflow.

## Phase 2 implemented

- Deterministic Risk Agent consuming the same evidence pack as Technical Agent.
- Downside, squeeze, and data-risk classifications.
- Explicit veto flag for failed evidence or severe downside risk.
- Stress scenarios for sudden reversal and liquidation squeeze.
- Challenge Agent checking OI ambiguity, missing liquidation/derivatives-volume evidence, venue concentration, confidence caps, and Technical/Risk contradictions.

## Phase 3 implemented

- Read-only comparator between v2 Technical/Risk/Challenge outputs and the latest legacy mechanism record.
- Explicit `agree`, `partial`, `disagree`, and `unavailable` states.
- Separate comparison of mechanism state and sudden-drop risk.

## Phase 4 implemented

- Paper-only Decision Synthesizer consuming Technical, Risk, Challenge, and Comparator outputs.
- Decision states: `OBSERVE`, `ALERT`, and `PAPER_PLAN`.
- Explicit gates: `blocked`, `paper-only`, and `human-approval-required`.
- Every decision has `approval_required=true` and `execution_allowed=false`.

## Phase 5 implemented

- Forward-outcome evaluator for every v2 decision at 1, 3, and 6 future bars.
- Directional accuracy for up/down signals.
- Forward return, maximum favorable excursion, and maximum adverse excursion.
- Risk-warning support metric for guarded decisions.
- `settled`, `pending`, and `insufficient_data` outcome states.

## Phase 6 implemented

- Append-only run ledger under `history/intelligence_v2/` (`run_ledger.json`, `decision_ledger.json`).
- Pending horizon settlement at 1, 3, and 6 bars using only post-decision prices.
- Persistent evaluation ledger and aggregate scorecard (`evaluation_ledger.json`, `scorecard.json`).
- Shadow runner calls `ledger.process_shadow_run()` after each run.
- Workflow persists only `history/intelligence_v2/` (no Telegram, no legacy history rewrite).
- Promotion to production still requires a separate approved pull request.

## Phase 7 next

Agent-specific scorecards (Technical / Risk / Challenge separately), longer replay windows, and optional scheduled shadow cadence without promoting alerts.
