# Intelligence v2 migration and rollback plan

## Current state

Legacy production remains the live alert path. v2 is shadow-only (manual or scheduled).

## Phase 9 implemented

- **Optional LLM interpreter enrichment** (`llm_enrich.py`): runs only if `OPENAI_API_KEY`, `XAI_API_KEY`/`GROK_API_KEY`, or `ANTHROPIC_API_KEY` is set; validates cited evidence IDs; falls back to deterministic text.
- **Multi-venue public adapters** (`venues.py`): Binance, OKX, Bybit, CoinGecko snapshots → `history/intelligence_v2/venue_snapshot.json`.
- **Dashboard promotion checklist**: `docs/index.html` section loads `promotion_report.json` and agent scorecards with a manual PR checklist.
- Still **no** Telegram, **no** auto-promote, **no** execution.

## Promotion (unchanged policy)

Metrics may reach `ready_for_human_review`. Production wiring requires a separate human-approved PR.

## Phase 10 — Forecast engine (shadow-only)

Implemented:

- `forecast.py`: deterministic multi-horizon forecasts (1/3/6/12 bars) with probability bands, expected move envelopes, invalidation, and first-class **ABSTAIN**.
- `forecast_eval.py`: append-only forecast ledger, horizon settlement, calibration table by probability band.
- Wired into `shadow_runner` (does **not** touch Telegram or legacy alerts).
- Confidence hard-capped; self-learning still disabled until walk-forward calibration is stable.

Artifacts:

- `history/intelligence_v2/forecast_ledger.json`
- `history/intelligence_v2/forecast_evaluation_ledger.json`
- `history/intelligence_v2/forecast_scorecard.json`

Promotion policy unchanged: human PR required; `execution_allowed` remains false.
