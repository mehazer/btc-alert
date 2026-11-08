# Recommended Simple Indicator Stack for Crypto Watch

**Prepared by Manus AI — 9 November 2026**

## Direct recommendation

Do not add a large collection of indicators or allow a general-purpose AI model to directly predict the next candle. The strongest defensible design for this system is a small, point-in-time feature set in which each feature has one job:

1. trend and market regime;
2. momentum or extension;
3. volatility and downside asymmetry;
4. participation and volume confirmation;
5. derivatives crowding;
6. BTC-relative strength and breadth;
7. event-based news impact.

The model should calculate and combine the numeric features deterministically. An LLM should classify news, extract facts, detect contradictions, and explain the evidence. It should not be trusted to calculate RSI, infer OI direction, or produce an uncalibrated “80% probability” from a chart.

This conclusion is deliberately more conservative than claiming that one of DeepSeek, Claude, Gemini, OpenAI, or Grok is the best crypto forecaster. The research reviewed does not provide a credible, apples-to-apples, out-of-sample crypto trading leaderboard for those models.

## What the evidence says about the five models

General model benchmarks are useful for reasoning, coding, tool use, and structured extraction. They are not evidence of crypto-market alpha. For example, the published comparison covering GPT-5, Gemini 2.5 Pro, Grok 4, DeepSeek R1 and other models tested 119 pediatric-dentistry multiple-choice questions in a controlled zero-shot setup. That is a useful example of reproducible model evaluation, but it does not measure market forecasting, regime change, data latency, transaction costs, or financial hallucination risk. [1]

Artificial Analysis is more relevant for selecting a general model because it reports independent evaluations across intelligence, agentic work, tool use, speed, and cost. Its index is still not a crypto-prediction test, so it should be treated as an engineering-selection input rather than a trading-performance result. [2]

The providers show that all five model families can be used as structured agents when given the right tools. DeepSeek documents reasoning effort and multi-turn tool calls. [3] Gemini documents function calling, structured output, parallel tool calls, and validation practices. [4] Grok documents configurable reasoning, tool calling, web search, and structured outputs. [5] OpenAI’s evaluation guidance emphasizes defining an evaluation task, running it on test inputs, grading outputs, and iterating from measured failures rather than relying on impressions. [6]

Anthropic’s finance-agent announcement is evidence that Claude has a mature finance-agent ecosystem and governed connectors, but its stated 64.37% Vals Finance Agent result is a vendor-reported claim and should not be interpreted as crypto forecasting accuracy. [7]

The practical conclusion is not “choose the highest-scoring chatbot.” Use the models by role and test them on the same private crypto evaluation set:

- **News and event extraction:** Claude, OpenAI, Gemini, DeepSeek, or Grok can all be candidates. Compare factuality, timestamp discipline, structured-schema compliance, and source attribution.
- **Live social context:** Grok may be useful when X/Twitter context is material, but social data must be treated as noisy evidence, not truth.
- **Low-cost batch classification:** DeepSeek may be attractive if its cost and deployment constraints fit the workflow.
- **Long-form synthesis and audit explanations:** Claude, OpenAI, and Gemini are reasonable candidates, but the winner should be determined by the repository’s own blind evaluation.
- **Numeric forecasting:** use deterministic code or a separately validated statistical model. No general LLM deserves authority here merely because it scores well on language or reasoning benchmarks.

The first model-selection test should therefore be a frozen set of historical crypto news items and market snapshots. Score each model on event type, asset relevance, impact direction, novelty, confidence, source quality, timestamp correctness, and abstention when evidence is insufficient. Only after that should model cost and response quality decide the production choice.

## The compact indicator stack

### 1. Trend regime: price relative to a moving average

Use one trend family, not several overlapping trend indicators. The recommended starting feature is the logarithmic distance between price and an EMA or SMA, together with the slope of that average. A practical 4-hour version is:

- `distance_50 = log(price / EMA50)`;
- `slope_50 = EMA50[t] / EMA50[t-6] - 1`;
- an optional slow-regime filter using EMA200 only for “trend” versus “range.”

The evidence base is stronger for price-to-moving-average ratios than for an isolated RSI number. A Financial Management study reported that price-to-moving-average ratios forecast daily Bitcoin returns in and out of sample and that associated strategies produced economically meaningful alpha and Sharpe improvements relative to buy-and-hold in its sample. That result should be treated as evidence for testing, not as a promise that the same edge remains today. [8]

This feature answers: “Is price aligned with a persistent regime?” It does not answer whether the next candle will rise. Its main value is preventing the system from treating every oversold or overbought reading as a reversal.

### 2. Momentum or extension: keep RSI, but demote it

Keep RSI(14) only as an extension and exhaustion feature. Do not use it as the main directional forecast. Store:

- RSI level;
- RSI slope over 2–3 bars;
- the asset’s rolling RSI percentile over approximately 90 observations;
- whether the current RSI is extreme relative to the prevailing trend regime.

An RSI of 67 means something different in a strong trend than in a range. The dashboard should therefore show RSI percentile and regime context, not just “RSI = 67.” Do not add Stochastic RSI, Williams %R, CCI, MACD, and several other oscillators at the same time. They mostly repeat the same price information and make the self-learning loop appear more certain than it is.

### 3. Volatility and downside asymmetry: use one risk family

Add a normalized volatility measure and a downside-specific measure:

- `realized_vol_12`: standard deviation of the last 12 four-hour log returns, annualization optional but consistent;
- `downside_vol_12`: standard deviation or mean square of only negative returns;
- `vol_percentile_90`: current realized volatility percentile versus the trailing 90 observations.

If high/low data becomes reliably available, ATR percentage is a useful simpler substitute for realized volatility. The important design choice is to separate “the market is moving a lot” from “the downside tail is becoming more active.” A peer-reviewed study of cryptocurrency volatility found that models including positive and negative semi-variances improved out-of-sample volatility forecasting, and that negative jumps were particularly important during crisis periods. [9]

This feature should primarily control risk sizing, alert severity, and invalidation distance. It should not automatically create a bullish or bearish signal.

### 4. Participation: relative volume, not raw volume

Use `RVOL = current volume / rolling median volume` or the rolling mean with winsorization. Keep the volume-price relationship simple:

- rising price plus high RVOL: participation confirms the move;
- rising price plus low RVOL: move is less confirmed;
- falling price plus high RVOL: distribution or liquidation pressure deserves attention;
- falling price plus low RVOL: potentially orderly retracement, not automatically bullish.

Do not use raw volume across assets or venues without normalization. Binance spot base volume is not directly comparable to OKX perpetual OI or another venue’s contract volume. The current repository correctly labels spot volume and derivatives OI as different datasets, but it needs normalized rolling features before using them in a combined score.

Order flow is a promising next layer. A 2024 SSRN study reports strong explanatory and out-of-sample predictive power from international currency order flow for cryptocurrency returns, with order flow outperforming economic fundamentals in its tested machine-learning settings. [14] It is not in the first implementation tier because reliable cross-venue aggressive-buy/sell or imbalance data is more difficult to timestamp and normalize than candles and volume. Add it only after the simpler volume and derivatives features have a clean baseline.

### 5. Derivatives crowding: use normalized funding plus OI, never OI alone

The minimum derivatives block should be:

- funding-rate percentile over the previous 7–30 days;
- change in open interest over 1, 3, and 6 bars;
- open interest normalized by price or market value where contract specifications permit;
- a four-state price/OI map: price up/OI up, price up/OI down, price down/OI up, price down/OI down.

Open interest is the count of outstanding contracts, not the number of contracts traded. Its economic meaning also varies by contract specification and venue. Coin Metrics explicitly warns that contract counts are not normalized across exchanges and that one-sided versus two-sided conventions can differ. [10]

Funding is a financing and positioning-pressure signal, not a direct long/short count. Binance describes funding as periodic transfers intended to keep perpetual prices anchored to the spot index; positive funding means longs pay shorts and negative funding means shorts pay longs. [11]

The interpretable rule is therefore:

- price up + OI up + funding normal: healthier continuation candidate;
- price up + OI sharply up + funding extreme: crowded continuation with elevated reversal/liquidation risk;
- price up + OI down: often short covering or position reduction, not automatically fresh demand;
- price down + OI up + funding negative/extreme: downside pressure may be crowded, so separate downside risk from short-squeeze risk;
- price down + OI down: deleveraging or liquidation; direction and reversal risk must be evaluated separately.

The system should aggregate OI and funding across Binance, OKX, and Bybit when possible. If only one venue is available, the conclusion must say “venue-specific.”

### 6. Basis: add only if the data is reliable

A spot/perpetual basis or futures annualized basis is more interpretable than another oscillator because it measures the price of leverage and carry. CME’s BasisWatch explicitly analyzes the relationship between futures and spot and its Implied Rate tool visualizes annualized spot/forward curves. [12]

For the first implementation, use one basis feature:

`annualized_basis = ((futures_price / spot_price) - 1) * 365 / days_to_expiry`

For perpetuals, use a clearly labelled premium or perp-spot spread rather than pretending it is equivalent to dated-futures basis. Do not combine CME basis, perp premium, funding, and several exchange spreads into one score until their economic overlap is measured.

### 7. BTC-relative strength and simple breadth

For ETH and SOL, retain the BTC overlay but make it quantitative:

- asset return minus BTC return over 3, 6, and 12 bars;
- asset price above/below its own EMA50;
- optionally, the percentage of a fixed liquid-asset universe above EMA50.

This is more useful than comparing BTC with itself. It distinguishes a market-wide BTC move from genuine asset-specific relative strength. The universe must be fixed in advance and recorded point-in-time to avoid survivorship bias.

### 8. News: replace generic sentiment with event impact

The current news design should not produce one undifferentiated sentiment number. News is more useful when represented as an event record with:

- `event_type`: regulation, ETF/institutional flow, exchange/custody, security exploit, protocol/project, macro/liquidity, legal, adoption, or rumor;
- `asset_scope`: market-wide, BTC, ETH, SOL, or unrelated;
- `direction`: positive, negative, mixed, or unknown;
- `impact_score`: expected market importance, separate from direction;
- `novelty_score`: new information versus repeated coverage;
- `source_quality`: primary filing/official statement, reputable reporting, specialist outlet, or social post;
- `cross_source_agreement`: independent confirmations, not duplicate syndication;
- `publication_time`, `event_time`, and `first_seen_time`;
- `time_decay`: event impact should decline unless there is a new development;
- `uncertainty` and an explicit abstain flag.

A single headline can be positive in direction but low in expected price impact. A security exploit can be negative for one token but irrelevant to BTC. A repeated article should not be counted as ten independent signals.

The empirical case for sentiment is mixed and asset- and horizon-dependent. One multi-asset study found Twitter sentiment predictive for BTC, BCH, and LTC, while a bullishness ratio helped for EOS and TRON; tweet volume was predictive only for LTC and XRP in that study. It also estimated that 1–14% of collected tweets were bot accounts. [13] That supports using sentiment as a conditional input with bot/duplication controls, not as a universal directional indicator.

Google Trends should remain an attention indicator. Use change and percentile of search interest, but do not treat high search interest as bullish. Attention can rise because of fear, liquidation, or scandal. Its best role is to increase the “event/attention regime” flag and trigger stricter news validation.

## What the current system is missing

The current architecture is directionally sound but the data layer is not yet strong enough for an 80% confidence claim:

- The sample BTC history contains rows with `open_interest: null`, so the derivatives block may be structurally unavailable rather than neutral.
- The system combines Binance spot volume with OKX perpetual OI and funding. That is acceptable as a labelled evidence pack, but not as a market-wide measure without venue aggregation and unit normalization.
- The historical walk-forward engine uses deterministic technical rules but does not reconstruct historical news or venue snapshots. Its accuracy therefore says little about news robustness.
- The Technical Agent’s mechanism interpreter can dominate the market-agent output. The recent robustness simulation confirmed that synthetic bullish and bearish news changed mean confidence but changed zero final decisions, while venue failure caused strong conservative gating. That is not necessarily wrong, but it means news currently has limited decision authority.
- Raw RSI is exposed without regime-relative normalization.
- There is no reliable basis, order-flow imbalance, liquidation time series, or true cross-venue derivatives volume in the current evidence contract.
- Confidence is a calibration statistic, not a probability of profit. The 80% threshold should not be treated as meaningful until there are substantially more settled, independent, regime-diverse observations.

## Recommended implementation order

### Stage 1: fix the measurement layer

Persist raw OHLCV fields, venue name, contract type, contract size or notional convention, funding interval, OI unit, retrieval time, and source response status. Replace missing derivatives fields with explicit `unavailable` states. Never turn unavailable into zero.

### Stage 2: add only the compact numeric features

Implement trend distance/slope, RSI percentile, realized and downside volatility, relative volume, normalized funding, normalized OI change, price/OI quadrant, basis where available, and BTC-relative return. Every feature should have a definition, unit, lookback, timestamp, source, and missing-data state.

### Stage 3: build the event-based news contract

Use an LLM only for structured extraction from timestamped articles. Require source URL, publication time, event time if known, asset scope, event type, direction, impact, novelty, confidence, and abstention. Deduplicate syndicated articles before scoring. Preserve the raw headline and the model version.

### Stage 4: validate by ablation, not intuition

Run the same point-in-time walk-forward with:

1. price-only baseline;
2. price plus trend and volatility;
3. add volume;
4. add derivatives;
5. add relative strength;
6. add event-based news;
7. add search attention.

For each increment, report directional accuracy, Brier score or calibration error, precision by confidence band, adverse excursion, drawdown, and performance after fees/slippage. Retain every trial, not just the best one. A feature earns production status only if its improvement survives multiple time splits, bull/bear/range regimes, symbols, and cost stress.

### Stage 5: select the model through a crypto-specific eval

Freeze a dated evaluation set. Score every candidate model on extraction accuracy, timestamp accuracy, source attribution, duplicate detection, abstention, structured-output validity, contradiction detection, latency, and cost. Use majority agreement only when models are independently grounded in the same source set; agreement between models reading the same bad headline is not independent confirmation.

## Checklist before changing the self-learning loop

- [ ] Every indicator has a precise definition, unit, lookback, source, and point-in-time timestamp.
- [ ] Missing, stale, venue-specific, and incomparable fields are explicit states.
- [ ] RSI and similar oscillators are contextual features, not standalone forecasts.
- [ ] OI is normalized and never interpreted as long/short direction by itself.
- [ ] Funding, OI, basis, and liquidation features are checked for overlapping information.
- [ ] News is represented as timestamped events with novelty, source quality, impact, and asset scope.
- [ ] Social sentiment is bot-filtered, deduplicated, and treated as conditional evidence.
- [ ] Google Trends is treated as attention, not direction.
- [ ] The LLM never calculates numeric indicators or silently fills missing market data.
- [ ] Walk-forward tests use only data available at the decision time.
- [ ] Ablation, regime, symbol, and cost-stress results are retained.
- [ ] Confidence is calibrated against outcomes and has a sample-size floor.
- [ ] No feature is promoted because it improves one historical slice.

## Bottom line

The highest-value improvement is not adding MACD, Stochastic RSI, more social feeds, or a more expensive model. It is improving the evidence contract and adding a small number of orthogonal features: trend regime, downside volatility, normalized derivatives crowding, relative strength, and event-based news impact. The current AI should be a disciplined evidence interpreter and verifier. Numeric prediction should remain deterministic and be promoted only after the ablation and walk-forward tests demonstrate stable incremental value.

## References

[1]: https://www.nature.com/articles/s41598-026-57392-7 "Benchmarking GPT-5, Gemini 2.5 Pro, Grok 4, and other LLMs on pediatric dentistry questions"

[2]: https://artificialanalysis.ai/ "Artificial Analysis independent model evaluations"

[3]: https://api-docs.deepseek.com/guides/thinking_mode/ "DeepSeek Thinking Mode and tool calls"

[4]: https://ai.google.dev/gemini-api/docs/function-calling "Gemini API function calling and structured output"

[5]: https://docs.x.ai/overview "Grok API overview and model capabilities"

[6]: https://platform.openai.com/docs/guides/evals "OpenAI evaluation guidance"

[7]: https://www.anthropic.com/news/finance-agents "Anthropic agents for financial services"

[8]: https://onlinelibrary.wiley.com/doi/abs/10.1111/fima.12310 "Learning and predictability via technical analysis: Evidence from bitcoin"

[9]: https://pmc.ncbi.nlm.nih.gov/articles/PMC8207820/ "Cryptocurrency volatility forecasting and positive/negative semi-variances"

[10]: https://gitbook-docs.coinmetrics.io/market-data/market-data-overview/market-open-interest "Coin Metrics market open interest definition and units"

[11]: https://www.binance.com/en/support/faq/detail/360033525031 "Binance Futures funding-rate mechanics"

[12]: https://www.cmegroup.com/markets/cryptocurrencies/cryptocurrency-basis-watch-and-implied-rate-tool.html "CME Cryptocurrency BasisWatch and Implied Rate Tool"

[13]: https://www.sciencedirect.com/science/article/abs/pii/S104244312030072X "The predictive power of public Twitter sentiment for forecasting cryptocurrency prices"

[14]: https://papers.ssrn.com/sol3/Delivery.cfm/5020002.pdf?abstractid=5020002&mirid=1 "Order Flow and Cryptocurrency Returns"
