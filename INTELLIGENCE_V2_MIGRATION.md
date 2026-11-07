# Intelligence v2 migration and rollback plan

## Current state

The legacy system remains the production system. Its scheduled workflows, Telegram notifications, dashboard, history writers, and self-improvement loop are unchanged by the v2 shadow implementation.

The v2 code is isolated under `intelligence_v2/`. Shadow mode (manual or scheduled) reads existing `history/` files, appends only to `history/intelligence_v2/`, and does not send Telegram messages, rewrite legacy history, access exchange credentials, or submit orders.

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

The legacy production path is preserved in the `main` branch. To disable v2 shadow scheduling, remove the cron from `shadow-intelligence.yml`. No scheduled production workflow imports `intelligence_v2` for alerts.

If v2 is later promoted, promotion must happen in a separate pull request with human approval.

## Phase 1-6 implemented

See prior commits: contracts, Risk/Challenge, comparator, paper decision, evaluation, persistent ledger.

## Phase 7 implemented

- Agent-specific scorecards in `history/intelligence_v2/agent_scorecards.json` (Technical, Risk, Challenge).
- Longer replay horizons: 1, 3, 6, 12, 24 bars (older decisions backfill new horizons as pending).
- Decision rows store per-agent snapshots (`agents.technical|risk|challenge`) at issue time.
- Optional scheduled shadow cadence: every 4 hours at :30 (`cron: 30 */4 * * *`), still no Telegram and no production promotion.
- Schema version 2; production alert path unchanged.

## Phase 8 next (ideal track)

AI Interpreter grounded by evidence IDs, Selected-Coin and Portfolio specialists, multi-venue data plane, and controlled promotion criteria from shadow scorecards into production.
