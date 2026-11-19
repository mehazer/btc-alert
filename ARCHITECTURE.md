# Architecture

What exists in the repository today, how the pieces are allowed to talk to each
other, and where the boundary between production and shadow runs.

Historical note: this document originally described a planned migration from a
single script to an agent layout, when the agent modules were placeholders. Those modules now exist and are wired into the pipeline. The design
rules below survived that migration; the file inventory did not.

## Layout

```
btc-alert/
├── alert_bot.py              Production entry point: data collection, RSI,
│                             history, Telegram cards
├── news_bot.py               News run: fetch, rank, categorise, optional AI
│                             summary, Telegram news card
├── news_sources.py           RSS and Telegram-preview source registry
├── news_intelligence.py      Ranking, deduplication, category rules
├── market_snapshot.py        Market-wide snapshot (news + social + technical)
├── build_dashboard_snapshot.py  Compact snapshot for the scorecard page
├── agent_calibration.py      Bounded weight calibration from settled outcomes
├── self_improvement.py       Forecast settlement and weight updates
├── date_window.py            Shared 24h/7d window helpers
├── fred_macro.py             Macro series
├── google_trends.py          Search-interest series
├── yahoo_news.py             Headline source
├── stocktwits_sentiment.py   Social sentiment source
├── agents/
│   ├── market_agent.py       Deterministic market-state read (RSI, OI, price,
│   │                         funding, news sentiment)
│   ├── technical_extras.py   Additional technical context
│   ├── mechanism_interpreter.py  Price/OI/volume relationship classifier
│   ├── positioning_regime.py     Funding/OI positioning regime
│   ├── news_sentiment.py     Headline sentiment
│   └── whale_agent.py        On-chain/whale shadow analysis
├── intelligence_v2/          Shadow intelligence stack (see below)
├── history/                  Git-tracked JSON store
├── docs/                     Static dashboard + reference documents
└── .github/workflows/        Pipeline and manual entry points
```

## intelligence_v2

The shadow stack, in data-flow order:

| Module | Responsibility |
|---|---|
| `evidence.py` | Normalise observations into an evidence pack with provenance |
| `technical.py` | EMA, Wilder RSI, ATR derived from completed candles |
| `interpreter.py`, `llm_enrich.py` | Optional narrative enrichment, evidence-ID validated |
| `coin_agent.py`, `portfolio_agent.py`, `comparator.py` | Per-symbol reads, portfolio aggregation, comparison |
| `challenge.py`, `risk.py` | Counter-evidence and risk assessment |
| `decision.py` | Single decision record with gate and invalidation |
| `ledger.py` | Append-only run, decision and evaluation ledgers, scorecards |
| `forecast.py`, `forecast_eval.py` | Multi-horizon forecasts, settlement, calibration |
| `metrics.py` | Neutral-band labels, naive baselines, balanced accuracy, reliability |
| `walkforward.py`, `replay.py`, `robustness.py` | Point-in-time backtest, replay, scenario stress |
| `promotion.py` | Gate that decides whether the shadow stack may be reviewed |
| `shadow_runner.py` | Orchestrator; never sends Telegram, never enables execution |
| `measure.py` | Read-only measurement report over committed ledgers |

## Agent contract

Agents return plain dictionaries. The keys are not identical across modules,
because each answers a different question; the shared ones are:

```python
{
    "state": str,            # agent-specific state label
    "confidence": int,       # 0-100
    "reasons": list[str],    # human-readable justifications, English
    "inputs": dict,          # raw inputs used, for audit
    "evaluated_at": str,     # ISO8601 UTC
}
```

`market_agent.evaluate()` adds `bias_score`, `bullish_points`, `bearish_points`,
`intensity` and `signal_weights_applied`. `whale_agent.evaluate(overview, stats)`
takes prepared inputs and returns its own distribution fields. `technical_extras`,
`positioning_regime`, `mechanism_interpreter` and `news_sentiment` expose
task-specific functions rather than a single `evaluate()`; check the module
docstring before calling.

Earlier revisions of this document described one uniform `risk_score` field.
That was the design intent, not the implementation; the text above matches the
code.

Agents do not import one another. They exchange data through these dictionaries,
so any agent can be tested alone and a failure in one cannot take down the
others.

## Isolation rules

1. **Agents stay independent.** No agent imports another agent.
2. **Production flow must not break.** A new agent is added as an isolated call
   inside `main()`, wrapped so that its failure does not stop RSI, history writes
   or Telegram delivery.
3. **Shadow never sends.** `intelligence_v2` has no Telegram path and
   `execution_allowed` is false everywhere.
4. **History writers share a concurrency group.** `crypto-history-writers`
   serialises runs that push to `history/`; overlapping pushes previously caused
   rejected updates.
5. **No database yet.** New state uses the same JSON-file pattern. A database is a
   separate decision, not an incidental one.

## Data flow

```
GitHub Actions (pipeline.yml, every 4h)
        │
        ├─ news_bot.py ──────────────► history/news_*.json ──┐
        ├─ alert_bot.py ─────────────► history/<SYMBOL>*.json┤
        │        └─ Telegram                                  │
        ├─ market_snapshot.py ───────► history/market.json    │
        ├─ agents/whale_agent.py ────► history/…              │
        └─ shadow_runner.py ─────────► history/intelligence_v2/
                                                 │
                                                 ▼
                                          docs/index.html,
                                          docs/history.html
                                          (client-side fetch)
```

The dashboard is static. It reads committed JSON through
`raw.githubusercontent.com` at page load; it holds no credentials and has no
backend.
