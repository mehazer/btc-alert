# Intelligence v2 migration and rollback plan

## Current state

The legacy system remains the production system. Its scheduled workflows, Telegram notifications, dashboard, history writers, and self-improvement loop are unchanged by the v2 shadow implementation.

The v2 code is isolated under `intelligence_v2/` and is only invoked by the manual-only `Intelligence v2 Shadow` workflow. It reads the existing `history/` files and writes only an ephemeral workflow artifact. It does not send Telegram messages, write legacy history, access exchange credentials, or submit orders.

## How to run v2 safely

From GitHub Actions, select **Intelligence v2 Shadow** and choose the timeframe and symbols. The output is uploaded as an artifact named `intelligence-v2-shadow-<run_id>`.

Locally:

```bash
python -m intelligence_v2.shadow_runner \
  --timeframe 4h \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,ZECUSDT,ZAMAUSDT \
  --output /tmp/intelligence-v2-shadow.json
```

## Rollback

The legacy production path is preserved in the `main` branch. To disable v2, do not run the manual shadow workflow. No scheduled workflow imports `intelligence_v2`.

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
- Manual-only GitHub Actions workflow with read-only repository permissions.

## Phase 2 next

The next safe increment is to add a separate Risk Agent and a Challenge Agent in shadow mode. They should consume the same evidence pack and return structured risk flags, stress scenarios, missing-data challenges, and invalidation conditions. They should not yet alter alerts or portfolio decisions.

After that, add a shadow comparator that measures where v2 and legacy disagree, then store the comparison as an artifact. Only after several weeks of comparison should the system consider a limited production integration.
