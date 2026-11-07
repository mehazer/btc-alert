# Crypto Watch system design

## Purpose

Crypto Watch is a deterministic market-observation and interpretation tool. It is not an execution or trading system. Every conclusion must be reproducible from the stored input snapshot, source timestamps, timeframe, code version, and calibration weights.

## Execution flow

1. `technical_agent.yml` runs every 4 hours or by manual dispatch.
2. `alert_bot.py` retrieves Binance spot candles/volume, OKX perpetual open interest and funding, the Alternative.me Fear & Greed index, liquidation/L-S extras when available, and the latest local news sentiment.
3. Each symbol observation is appended to `history/<SYMBOL>.json`. No current implementation trims old records.
4. `market_agent.py` evaluates BTC-level directional state from RSI, sentiment, funding, price/OI changes, and news sentiment.
5. `mechanism_interpreter.py` classifies price/OI/volume relationships: fresh long buildup, short covering, spot-led buying, weakening participation, or mixed/inconclusive. OI alone is never treated as long/short direction.
6. `positioning_regime.py` calculates funding percentile, recent OI change, positioning pressure, regime, and separate squeeze risk. Crowded shorts are not automatically treated as downside risk.
7. `self_improvement.py` settles older forecasts after three later observations, records outcomes, calculates direction/risk accuracy, and changes evidence weights by at most 0.02 within 0.50–1.50 bounds.
8. The workflow commits all `history/` records. Technical and news workflows share the `crypto-history-writers` concurrency group so their Git pushes do not overwrite each other.
9. `check-news.yml` runs every 4 hours, writes categorized news and daily Trends results, and sends one compact Telegram news update.
10. GitHub Pages deploys automatically from repository updates; it is not a market-analysis heartbeat and therefore has no independent empty scheduled run.
11. GitHub Pages serves `docs/index.html` for current state and `docs/history.html` for full audit/history.

## Data sources and definitions

| Input | Source | Meaning | Limitation |
|---|---|---|---|
| Price and volume | Binance spot | Latest configured candle close and base-asset volume | Spot only; not derivatives volume |
| OI and funding | OKX perpetual | Open interest and funding for the mapped perpetual | Venue-specific; OI units are coin-denominated |
| Fear & Greed | alternative.me | External 0–100 sentiment index | Sentiment proxy, not a forecast |
| L/S and liquidations | Exchange/API extras | Supplemental positioning/liquidation evidence | May be unavailable |
| News | Configured RSS/web sources | Deduplicated, ranked crypto headlines | Headlines do not prove causality |
| Google Trends | pytrends | Daily normalized search interest 0–100 | Can be unavailable/rate-limited |

## Prediction and evaluation contract

At every technical run, each successfully analyzed symbol creates a prediction record containing:

- run ID, UTC observation time, symbol, timeframe and entry price;
- predicted direction from the observed price direction;
- sudden-drop risk and separate crowded-short/squeeze-risk flags;
- the exact evidence families available at decision time;
- the calibration-weight snapshot used for that run.

After three later observations for the same symbol are available, the record is settled. Direction is a hit when the three-observation forward return has the forecast direction, using a ±0.15% flat band. A risk event is observed when the maximum adverse move reaches at least 1%. Risk accuracy measures whether the high-risk/low-risk classification matched that event definition. These are evaluation labels, not trading recommendations.

Confidence is a calibration statistic, not a probability of profit. It is a blend of 65% direction accuracy and 35% risk accuracy. Until at least 10 settled samples exist, confidence is capped at 65%. The target threshold is 80%; the dashboard shows whether it has actually been reached.

## Weight updates

The loop tracks six evidence families: price action, OI context, volume confirmation, funding context, BTC overlay, and positioning context. For each settled forecast, an available family receives a +0.02 update when the directional forecast was correct and -0.02 when it was wrong. Weights are bounded at 0.50 and 1.50. The positioning weight changes the threshold used for **long-side** downside-risk adjustment. Crowded-short evidence is surfaced as squeeze risk rather than being silently converted into drop risk.

This is intentionally a bounded online calibration loop. It does not rewrite old results, retrain a black-box model, or use future observations while making the original decision. Weight changes and metrics are written to `history/model_calibration.json` and each run records its snapshot in `history/evaluation_runs.json`.

## Historical replay track

`python -m intelligence_v2.replay` runs a read-only settlement replay over the existing v2 decision identities and historical symbol observations. It uses the same point-in-time evaluator as live settlement, writes to `history/intelligence_v2/replay/<batch-id>/`, and labels every output `evaluation_mode=historical_replay` with a replay batch ID and `lookahead_safe=true`.

Replay is diagnostic only. It must not rewrite `decision_ledger.json`, `evaluation_ledger.json`, `scorecard.json`, agent scorecards, or the promotion report. Live shadow metrics remain the evidence used by the promotion gate; replay metrics are used to validate settlement logic, inspect longer horizons, and compare robustness across historical data. Replay of existing decisions does not regenerate historical agent decisions, so it is not a substitute for a future full point-in-time decision backtest.

`python -m intelligence_v2.walkforward --symbols BTCUSDT,ETHUSDT,SOLUSDT` performs that next-level diagnostic. At each eligible historical bar it writes a history prefix, rebuilds the evidence pack, runs Technical, Risk, Challenge, Comparator, and Decision Synthesizer, then evaluates the resulting decision at every configured horizon. It writes only to `history/intelligence_v2/walkforward/<batch-id>/` and the stable `walkforward/latest/` pointer. The batch ID and output hash are derived from the source history and configuration, so reruns are deterministic. ZEC and ZAMA remain excluded until their history depth is sufficient. Legacy date-only rows receive monotonic synthetic timestamps that preserve source order; original timestamps remain in each record for audit.

`python -m intelligence_v2.robustness --symbols BTCUSDT,ETHUSDT,SOLUSDT` runs controlled robustness scenarios over the same point-in-time path: baseline, bullish synthetic news shift, bearish synthetic news shift, venue disagreement, and combined bearish-news/venue-failure stress. Synthetic inputs are explicitly labelled and are not presented as reconstructed headlines or historical exchange snapshots. The report compares confidence, decision changes, risk vetoes, and horizon outcomes against baseline. It is diagnostic only and cannot update live weights or promotion status.

## History files

- `history/<SYMBOL>.json`: append-only market observations.
- `history/market_agent_history.json`: append-only BTC state evaluations.
- `history/mechanism_history.json`: append-only symbol mechanism/risk evaluations.
- `history/evaluation_runs.json`: run-level audit ledger with inputs, outputs, sources and weights.
- `history/prediction_ledger.json`: every forecast and its decision-time snapshot.
- `history/evaluation_results.json`: settled outcomes and hit/miss labels.
- `history/model_calibration.json`: current bounded weights, threshold and metrics.
- `history/news_runs.json`: every hourly news-agent execution, source count, category count and service statuses.
- `history/edge_stats.json`: legacy mechanism forward-return aggregates.

## Telegram policy

The technical workflow sends one compact BTC market-state message and one primary-symbol mechanism message by default. All-symbol mechanism messages require explicit `NOTIFY_ALL_SYMBOLS=true`. Exact duplicate messages are skipped within a run. The hourly news workflow sends one compact news summary and removes repeated key-point/category headlines. Missing Telegram secrets never destroy the stored analysis.

## Maintenance checklist

When changing an interpretation rule, update this document, add the rule to the relevant module's docstring/comments, preserve the old history, increment the model version in `self_improvement.py`, and run syntax/schema checks. Never delete history to make a dashboard look cleaner; use `docs/history.html` and filters/pagination instead.
