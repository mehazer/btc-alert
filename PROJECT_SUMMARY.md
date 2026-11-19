# Project Summary

**Status: historical snapshot.** Written when the repository was a single RSI
script. It is kept because the weakness analysis below still applies. Facts that
have changed since are marked *(updated)* and the current entry point is
`README.md`.

## 1. What this system is

`btc-alert` collects public crypto market data — Binance spot candles, OKX
perpetual open interest and funding, the Alternative.me Fear & Greed index, RSS
headlines — sends a Telegram summary, and publishes a static dashboard on GitHub
Pages. It runs entirely on free infrastructure with no server.

Architectural stance: serverless; credentials live only in GitHub Secrets; no
component requires a persistent host.

## 2. Module inventory

### 2.1 `alert_bot.py` — production orchestrator

| Function area | Job | Source | Behaviour on failure |
|---|---|---|---|
| `fetch_binance` | Candles and volume | Binance spot (`data-api.binance.vision`) | Raises; the caller reports the error and skips the symbol |
| `fetch_okx` | Open interest and funding | OKX perpetual swap *(updated: earlier Bybit)* | Returns `None`; symbol still processed |
| `fetch_fear_greed` | Market-wide sentiment | alternative.me | Returns `None`; symbol still processed |
| `compute_rsi` | Wilder RSI, no external TA library | Candle closes | Needs `period + 1` closes |
| history helpers | Read/write per-symbol series | `history/<SYMBOL>.json`, `history/<SYMBOL>_4h.json` | Corrupt JSON starts fresh |
| `send_telegram` | Delivery | Telegram Bot API | Logged, never crashes the run |
| `process_symbol` | Full per-symbol flow | the above | Returns false on critical error |

Behavioural detail that matters: `ALWAYS_NOTIFY` (default false) sends a message
even when no threshold is crossed. It exists for pipeline testing and should stay
false in production.

### 2.2 Workflows

*(updated: scheduling changed)*

| Workflow | Trigger | Purpose |
|---|---|---|
| `pipeline.yml` | every 4h, manual | Single scheduled owner: news → technical → market → whale → v2 → dashboard |
| `technical_agent.yml` | manual | RSI/Telegram debug run |
| `check-news.yml` | manual | News debug run |
| `shadow-intelligence.yml` | manual | v2 shadow run for one timeframe/symbol set |

All writers that touch `history/` share the `crypto-history-writers` concurrency
group. Without it, two overlapping runs produced rejected pushes — this happened
once and was fixed.

### 2.3 Dependencies

*(updated)* `requests`, `beautifulsoup4`, `pytrends`. Deliberately small: no TA
library, to keep Action installs fast and unbreakable.

### 2.4 Dashboards

Static, client-side, no backend and no credentials. JavaScript fetches committed
JSON from `raw.githubusercontent.com` at page load and renders it. Data freshness
therefore depends on the run cadence and CDN cache behaviour: this is a periodic
snapshot, not a live feed.

### 2.5 Data store

`history/` is the database. Per-symbol series are capped (200 rows for the legacy
series, 500 for the rich series); `market.json` keeps one record; the v2 ledgers
are append-only.

### 2.6 Secrets

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and optionally `NEWS_AI_API_KEY`,
`NEWS_AI_BASE_URL`, `NEWS_AI_MODEL`. No value is hardcoded anywhere in the
repository.

## 3. Data flow

```
GitHub Actions
     │
     ├─ Binance spot ─┐
     ├─ OKX perpetual ─┼─► alert_bot.py ─► history/<SYMBOL>*.json ─► commit
     ├─ alternative.me ┤        └─► Telegram
     └─ RSS feeds ─────┘
                                   │
                    docs/index.html ◄─ raw.githubusercontent.com
```

## 4. Weaknesses and scaling risks

Ordered by how likely they are to bite.

### 4.1 Chained dependency on free public APIs (high)

Binance Futures (451) and then Bybit (403) both blocked shared GitHub Actions
IPs at different points. Any RSS source behind Cloudflare can do the same. The
system has no SLA at any link in the chain, and that is the largest fragility as
ambitions grow.

### 4.2 Git-tracked JSON instead of a database (medium-high, grows over time)

- No query layer: no indexing, filtering or aggregation.
- Repository size grows with every commit, which slows clones and CI.
- No safe concurrent writes; the concurrency group mitigates but does not solve.
- Schema is not versioned, so a renamed field can silently break dashboard
  rendering.

### 4.3 Silent failure (medium)

A failed run is visible only as a red X in the Actions tab. Nothing notifies the
operator that the bot did not run. A system that claims continuous observation
should report its own absence.

### 4.4 No retry or backoff (low-medium)

Each API call is attempted once. A transient error is indistinguishable from a
real outage in the logs.

### 4.5 Monolithic entry point (scaling risk, not a production bug)

Collection, computation, delivery and presentation still share `alert_bot.py`.
It is function-modular and works, but the layers now want physical separation;
`intelligence_v2/` is the first module that does this properly.

### 4.6 Dashboard depends on raw-content rate limits (low today)

Unauthenticated `raw.githubusercontent.com` requests are rate-limited per IP.
Fine for one reader, fragile if the page is shared or refreshed aggressively.

### 4.7 Test coverage (was low; partially addressed)

Production code still has no test suite. `intelligence_v2` now has unit tests for
the measurement primitives (`python -m unittest discover -s tests`), and the
contract-style checks live in that package. Treat any change to `alert_bot.py` as
unverified until the pipeline runs.
