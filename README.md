# BTC Alert

A free, serverless crypto market observation pipeline. GitHub Actions collects
price, derivatives, news and macro data every four hours, sends a Telegram
summary, and writes the evidence trail back into the repository so every
conclusion can be audited later.

This repository does not trade. It observes, records, and evaluates its own
forecasts.

## Two layers, one boundary

| Layer | What runs | Sends Telegram | Changes its own weights |
|---|---|---|---|
| Production | `alert_bot.py`, `news_bot.py`, `market_snapshot.py` | Yes | No |
| Shadow (v2) | `intelligence_v2/*` | **No** | Only inside bounded calibration, never on history |

The shadow layer is the measurement experiment: it writes decisions, forecasts
and evaluation ledgers, and only its own scorecards consume them. A promotion
gate can mark it `ready_for_human_review`; wiring it into production requires a
separate human-approved pull request.

## What a run does

`.github/workflows/pipeline.yml` runs at `0 */4 * * *` and on manual dispatch:

1. **News** — `news_bot.py` ranks headlines by source weight and novelty,
   deduplicates, categorises, and optionally adds an AI summary.
2. **Technical / alerts** — `alert_bot.py` pulls Binance spot candles, OKX
   perpetual open interest and funding, and the Alternative.me Fear & Greed
   index, computes Wilder RSI, and sends the Telegram card.
3. **Market snapshot** — `market_snapshot.py` combines news, social and technical
   state into `history/market.json`.
4. **Whale / on-chain (shadow)** — `agents/whale_agent.py` reads free public
   APIs; no Telegram output.
5. **Intelligence v2 (shadow)** — evidence → agents → decision → ledger →
   forecast → settlement.
6. **Dashboard snapshot** — a compact JSON so the scorecard page never loads the
   multi-megabyte decision ledger.

Scheduled runs exist only in `pipeline.yml`. `check-news.yml`,
`technical_agent.yml` and `shadow-intelligence.yml` are manual-only debugging
entry points; their old schedules were removed so the pipeline owns the cadence.

## Symbols and timeframe

`SYMBOLS` and `INTERVAL` are environment variables. Defaults are
`BTCUSDT,ETHUSDT,SOLUSDT` at `4h`. The pipeline workflow sets five symbols:
`BTCUSDT,ETHUSDT,SOLUSDT,ZECUSDT,ZAMAUSDT`. `RSI_THRESHOLD` is set to `70` there.

## Data store

| Path | Contents | Retention |
|---|---|---|
| `history/<SYMBOL>.json` | Legacy observation series | last 200 rows |
| `history/<SYMBOL>_4h.json` | Rich series with OI and funding | last 500 rows |
| `history/market.json` | Latest market-wide snapshot | single record |
| `history/intelligence_v2/` | Run, decision, evaluation, forecast ledgers and scorecards | append-only |

History is committed back to the repository by the workflow. There is no
database: git is the store, so a clone carries the full audit trail and the full
size of it.

## Measurement

`docs/MEASUREMENT.md` defines how every number is produced. Two rules matter when
reading the scorecards:

- Accuracy is reported against naive baselines (`always_up`, `always_down`,
  `persistence`, `ema_slope`). A hit rate without a baseline cannot separate
  skill from market drift.
- Outcomes are labelled with a volatility-scaled neutral band, so a move inside
  the noise floor is reported as `neutral` rather than scored as a wrong call.

`python -m intelligence_v2.measure --history-dir history` recomputes all of it
from the committed ledgers without touching them.

## Setup

1. Create a Telegram bot with **@BotFather**, start a chat with it, and read your
   `chat_id` from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
2. Add repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
   (Settings → Secrets and variables → Actions).
3. Optional AI summary: `NEWS_AI_API_KEY`, and optionally `NEWS_AI_BASE_URL`,
   `NEWS_AI_MODEL`.
4. Trigger the pipeline once from the Actions tab and confirm the RSI line in the
   logs.

No keys belong in the repository. Every credential is read from the environment.

## Documentation map

Documents have distinct jobs. Where a document disagrees with the code, the code
wins and the document is a bug.

| Document | Role | Status |
|---|---|---|
| `README.md` | Entry point: what runs, when, and where the data lives | Current |
| `SYSTEM_DESIGN.md` | Execution flow, data-source definitions, prediction and evaluation contract | Current |
| `docs/MEASUREMENT.md` | How accuracy, baselines and calibration are computed | Current |
| `docs/DEVELOPMENT_PRINCIPLES.md` | Working rules for changes to this repository | Current |
| `ARCHITECTURE.md` | Module map, agent contract, isolation rules | Current |
| `PROJECT_SUMMARY.md` | Module inventory and known weaknesses at time of writing | Historical snapshot |
| `ROADMAP.md` | Open items and completed work | Living, updated when work lands |
| `INTELLIGENCE_V2_MIGRATION.md` | Migration and rollback plan for the shadow layer | Historical record |
| `IDEAL_CRYPTO_AGENT_ARCHITECTURE.md` | Target design, explicitly not current state | Aspirational |
| `docs/INDICATOR_RESEARCH_RECOMMENDATION.md` | Research memo on which indicators to keep | Reference |

## Known limits

- Free public endpoints rate-limit shared runner IPs; a source failure is logged
  and skipped, not retried indefinitely.
- Git as a data store grows without bound unless retention is enforced; see the
  table above for the per-file caps that exist today.
- Scheduled GitHub Actions are not real-time; a run can start late under load.
- v2 accuracy is measured on one regime at a time. Read
  `measurement.regime` before reading any accuracy figure.
