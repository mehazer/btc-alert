# Ideal Crypto Intelligence System

## Main conclusion

You are on the right path, but the current system is still a **single scheduled collector with several analysis functions**, not yet a true multi-agent platform. That is not a failure. It is the correct foundation to build on.

The most important architectural correction is this:

> Do not make every component an autonomous LLM agent. Make data collection, normalization, calculations, risk limits, scoring, evaluation, and scheduling deterministic. Use AI agents where interpretation, prioritization, conflict resolution, and research flexibility are genuinely useful.

Anthropic’s current guidance makes the same distinction between predictable workflows and flexible agents: workflows are better when the path is known, while agents are useful when the required steps cannot be predetermined. It also recommends starting with simple composable patterns rather than adding framework complexity prematurely. [1]

Your target should therefore be a **workflow-controlled, multi-specialist intelligence system** with a central evidence contract, not a group of uncoordinated bots exchanging free-form text.

## Target visual architecture

![Ideal crypto multi-agent architecture](/home/ubuntu/work/btc-alert/docs/ideal-crypto-agent-architecture.png)

The editable diagram is available as [Mermaid source](/home/ubuntu/work/btc-alert/docs/ideal-crypto-agent-architecture.mmd).

## What the system should contain

### 1. Data and evidence plane

All agents should consume the same normalized evidence layer. They should not independently call exchanges and calculate slightly different versions of “current price,” “OI change,” or “volume.” Independent retrieval is one of the fastest ways to create contradictory conclusions.

The data plane should have five layers:

1. **Raw observations:** exact exchange responses, article payloads, publication timestamps, retrieval timestamps, venue, instrument, unit, and source status.
2. **Normalized observations:** common symbol identifiers, UTC timestamps, quote/base units, contract type, venue, and candle interval.
3. **Feature calculations:** returns, ATR, realized volatility, RSI, OI changes, funding percentiles, liquidation imbalance, basis, volume ratios, market breadth, and correlation.
4. **Quality gates:** freshness, missingness, outlier detection, timestamp alignment, venue disagreement, and stale-feed detection.
5. **Point-in-time evidence packs:** the exact data available to an agent at the moment it made a conclusion.

Your current Binance spot plus OKX perpetual arrangement is acceptable for a prototype, but it is not a complete market view. Binance’s derivatives API and OKX’s REST/WebSocket APIs expose important market-data capabilities, but each feed remains venue-specific. OKX explicitly documents both REST and WebSocket market access and recommends WebSocket for market data and order-book depth. [2] [3]

For serious analysis, add at least two venues for derivatives and preserve venue identity. A sudden increase in OKX OI is not automatically a whole-market increase in OI.

### 2. Specialist analysis plane

The specialist agents should have narrow responsibilities and stable output contracts.

**Technical Agent** should interpret price action, OI, funding, volume, volatility, market breadth, and multi-timeframe structure. It should produce scenarios, not a single prediction.

**News Agent** should retrieve, deduplicate, classify, score source quality, identify affected assets, distinguish fact from commentary, and estimate possible market impact. It should never convert a headline into a trade signal without a market confirmation step.

**Risk Agent** should estimate downside, squeeze, liquidation, gap, liquidity, correlation, and data-quality risk. It should be allowed to veto an alert even when the Technical Agent is bullish.

**Selected-Coin Agent** should maintain a coin-specific thesis: drivers, liquidity, structure, token unlocks, listings, ecosystem events, invalidation levels, and whether the move is simply beta to BTC.

**Portfolio Agent** should evaluate exposure, concentration, correlation, leverage, stablecoin risk, and scenario losses. It should not decide entries from technical signals alone.

**Regime Agent** should determine whether the environment is trend, range, high-volatility, low-liquidity, BTC-led, alt-led, macro-led, or event-driven.

These agents should not communicate by passing long natural-language messages. They should exchange versioned JSON evidence and conclusions. Natural-language explanations can be generated later for the user.

A recommended common contract is:

```json
{
  "agent": "technical",
  "run_id": "2026-11-05T10:00:00Z-abc123",
  "symbol": "ZECUSDT",
  "timeframe": "4h",
  "state": "spot-led-recovery",
  "scenarios": [
    {"name": "continuation", "probability_band": "medium", "conditions": []},
    {"name": "failed_breakout", "probability_band": "medium", "conditions": []}
  ],
  "risk_flags": ["low_derivatives_confirmation"],
  "confidence": 58,
  "invalidation": ["4h close below 1180"],
  "evidence_ids": ["obs-123", "feature-456"],
  "evaluated_at": "2026-11-05T10:00:00Z",
  "model_version": "technical-v1.2"
}
```

### 3. Interpretation and decision plane

The AI Interpreter should not be the primary calculator. Its job is to explain what the deterministic agents found, identify conflicts, compare scenarios, and write a concise human-readable interpretation.

Use a separate **Challenge Agent** to ask:

- Are the sources fresh and aligned?
- Is this conclusion based on one venue only?
- Is OI being misread as long/short direction?
- Is a BTC-led move being mistaken for coin-specific strength?
- Is the confidence higher than the historical calibration supports?
- What evidence would invalidate the conclusion?

The Decision Synthesizer should combine specialist outputs into one of four states:

- **Observe:** evidence is incomplete or conflicting.
- **Alert:** a meaningful market development exists, but no plan is proposed.
- **Paper plan:** a scenario, trigger, invalidation, and risk budget are defined for simulation only.
- **Human approval required:** a portfolio-impacting decision would be considered.

Do not allow any agent to place trades. Keep exchange API keys read-only until a separate, manually approved execution system exists. OKX’s documentation separates read, trade, and withdrawal permissions, and recommends IP binding for API keys. [3]

OpenAI’s agent documentation supports orchestration, handoffs, tracing, sessions, guardrails, testing, and visualization, but those capabilities should sit around your domain contracts rather than replace them. [4]

## Timeframe design

A single 6-hour timeframe is not enough for the complete system. It is useful for a medium-term regime view, but risk and execution context need faster data, while portfolio decisions need slower context.

Use a hierarchy:

- **1-minute or 5-minute:** liquidation bursts, spread, order-book imbalance, abnormal volume, and event reaction. Do not use this alone for a directional thesis.
- **15-minute:** intraday momentum, breakout confirmation, and short-lived positioning changes.
- **1-hour:** tactical trend, funding/OI changes, and news reaction.
- **4-hour:** primary swing structure and the default Technical Agent decision timeframe.
- **6-hour:** optional regime and medium-term participation view; keep it as a derived view, not the only source timeframe.
- **1-day:** macro trend, volatility regime, market breadth, portfolio allocation context, and thesis review.
- **1-week:** strategic exposure review and model drift analysis.

Every signal must say which timeframe produced it. The system should not merge a 5-minute liquidation event with a 4-hour trend conclusion without labeling the difference.

## What should be paid for first

Do not start by buying many AI subscriptions. The highest-value investment is usually better data and evaluation infrastructure.

Recommended order:

1. **Reliable multi-venue historical data** with candles, trades, OI, funding, liquidations, basis, and consistent timestamps.
2. **Persistent time-series storage** instead of Git-tracked JSON as the main database. TimescaleDB is a reasonable Postgres-based option because it supports time-series analytics, continuous aggregates, compression, backups, and vector extensions. [5]
3. **LLM API access** through a provider-neutral adapter. Use a low-cost model for extraction, categorization, and routine summaries. Use a stronger reasoning model only for cross-agent synthesis, difficult conflict resolution, and research.
4. **Tracing and evaluation.** MLflow’s current evaluation guidance emphasizes datasets, scorers, human feedback, LLM-as-a-judge, systematic evaluation, and production monitoring. [6]
5. **Observability and alert reliability:** feed freshness, failed jobs, API latency, token cost, duplicate alerts, and false-alert rate.

A paid AI subscription for interactive use is not the same as an API plan suitable for unattended automation. For background agents, you need API billing, rate limits, logging, model version control, and cost controls.

## Self-learning: what it should and should not mean

The current self-improvement loop is a useful beginning, but it should be expanded in three stages.

### Stage 1: Outcome evaluation

Keep the current approach: every conclusion stores its inputs, timestamp, model version, confidence, invalidation, and forward outcome. Evaluate direction, drawdown, risk-event detection, and calibration.

### Stage 2: Agent-specific scorecards

Do not use one combined confidence number. Track separately:

- Technical Agent directional accuracy by symbol and timeframe.
- News Agent catalyst relevance and false-catalyst rate.
- Risk Agent downside-risk precision and recall.
- Portfolio Agent scenario loss error.
- AI Interpreter factuality, evidence coverage, and unsupported-claim rate.

### Stage 3: Controlled promotion

Never let a self-learning loop silently rewrite production logic after one or two outcomes. Use shadow mode first, then compare the new version against the current version on the same replay data. Promote a new prompt, model, or weight set only when it improves a predefined score without materially increasing false alerts or risk misses.

The loop should optimize for **calibration and decision quality**, not for the highest raw hit rate. A model can achieve a high hit rate by making vague predictions or by avoiding difficult regimes.

## Proposed repository evolution

Your current `alert_bot.py` is already doing too much. The next refactor should create these boundaries:

```text
btc-alert/
├── apps/
│   ├── technical_runner.py
│   ├── news_runner.py
│   └── evaluation_runner.py
├── agents/
│   ├── technical_agent.py
│   ├── news_agent.py
│   ├── risk_agent.py
│   ├── portfolio_agent.py
│   ├── coin_agent.py
│   ├── regime_agent.py
│   ├── interpreter_agent.py
│   └── challenge_agent.py
├── contracts/
│   ├── observation.py
│   ├── evidence_pack.py
│   ├── agent_output.py
│   └── decision.py
├── data/
│   ├── exchange_adapters/
│   ├── news_adapters/
│   ├── normalization.py
│   └── quality.py
├── features/
├── orchestration/
├── evaluation/
├── storage/
├── prompts/
├── dashboards/
└── workflows/
```

Keep JSON exports for GitHub Pages, but make the database the source of truth once history grows. GitHub Actions is suitable for scheduled prototypes, but an event-driven worker or managed scheduler will eventually be more reliable for WebSocket ingestion, minute-level events, retries, and long-running evaluation.

## Recommended implementation sequence

### Phase 0: prove the foundation

For one week, do not add new agents. Measure feed freshness, missing-data rate, duplicate alerts, runtime failures, and whether the current Technical and News Agents produce reproducible outputs.

### Phase 1: introduce canonical contracts

Create typed schemas for observations, evidence packs, agent outputs, scenarios, risk flags, decisions, and outcomes. Add a run ID and evidence IDs everywhere.

### Phase 2: split risk from technical analysis

Build a real Risk Agent before Portfolio Agent. It should produce stress cases and veto conditions, not just another bullish/bearish score.

### Phase 3: add the AI Interpreter and Challenge Agent

Use AI only after the deterministic evidence pack is complete. Require citations to evidence IDs and require an explicit “unknown” response when evidence is missing.

### Phase 4: add portfolio and selected-coin agents

Start with paper positions and a manually entered portfolio. Do not connect execution keys. Evaluate exposure and scenario loss before attempting any sizing recommendation.

### Phase 5: move to persistent storage and replay

Store raw data, normalized data, features, outputs, traces, and outcomes in a time-series database. Build a replay runner that reconstructs exactly what each agent could have known at historical decision time.

### Phase 6: controlled model comparison

Run two model/prompt versions in shadow mode. Compare factuality, evidence coverage, calibration, false alerts, latency, and cost. Only then promote a new version.

## Current assessment checklist

- [x] Good initial direction: separate technical and news responsibilities.
- [x] Good decision to preserve history and evaluate previous conclusions.
- [x] Good decision to keep Telegram and dashboard outputs separate from core calculations.
- [ ] Central canonical evidence contract.
- [ ] Multi-venue normalized data.
- [ ] Point-in-time feature store.
- [ ] Real Risk Agent separated from Technical Agent.
- [ ] Portfolio Agent with exposure and scenario accounting.
- [ ] AI Interpreter grounded by evidence IDs.
- [ ] Challenge Agent and veto logic.
- [ ] Agent-specific evaluation datasets and scorecards.
- [ ] Persistent time-series database.
- [ ] Replay/backtest engine with no look-ahead bias.
- [ ] Model/prompt registry and shadow-mode promotion.
- [ ] Read-only API security and human approval gates.

## References

[1]: https://www.anthropic.com/research/building-effective-agents "Building effective agents"

[2]: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data "Binance Futures market data documentation"

[3]: https://www.okx.com/docs-v5/en/ "OKX API documentation"

[4]: https://openai.github.io/openai-agents-python/multi_agent/ "OpenAI Agents SDK orchestration documentation"

[5]: https://docs.timescale.com/ "TimescaleDB documentation"

[6]: https://mlflow.org/docs/latest/genai/eval-monitor/ "MLflow evaluation and monitoring for LLMs and agents"
