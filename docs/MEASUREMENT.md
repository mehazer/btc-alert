# Measurement

How v2 numbers are produced, and how to read them. Every figure here is
computed from the committed ledgers; nothing in this document is a claim about
market edge.

## Why this exists

The v2 scorecard used to report one number: direction accuracy. Against the
first 990 settled records that number was 41.12%, which reads like a failing
signal. It was not a clean measurement:

- Every non-zero forward return was labelled up or down, so a 0.05% move inside
  the 4h noise floor counted as a wrong call.
- No baseline was reported. Over a sample where price fell 17-27% on all five
  assets, "always down" scores 82%, and a 41% hit rate means something very
  different from 41% in a flat market.
- Confidence and stated probability had never been compared with observed hit
  rates.
- 990 rows were presented as if they were 990 independent trials. They come from
  five assets over eleven days in a single downtrend.

## Labelled outcomes

`intelligence_v2/metrics.py` labels each settled horizon with three classes:

```
band = k * sigma * sqrt(horizon_bars)
label = up      if forward_return > band
        down    if forward_return < -band
        neutral otherwise
```

- `sigma` is the standard deviation of the trailing 20 close-to-close returns,
  taken only from bars at or before the decision timestamp.
- `k` defaults to 0.5 and is configurable with `V2_NEUTRAL_BAND_K`.
- ATR cannot be used: committed history rows carry no high/low, so close-to-close
  sigma is the available volatility estimate. This is stated in every scorecard
  under `measurement.labeling`.

A neutral outcome is **not** a wrong call and **not** a hit. It is excluded from
accuracy and reported as `neutral_rate_pct` / `directional_coverage_pct`, so a
scorecard always shows how much of the sample was actually scored.

## Baselines

For the rows the model called, the same labels are scored against:

| Baseline | Rule |
|---|---|
| `always_up` | predict up on every row |
| `always_down` | predict down on every row |
| `persistence` | repeat the last completed bar's direction |
| `ema_slope` | follow the change in EMA(20) over the final bar |

`model_skill_vs_best_baseline_pct` is the model's accuracy above the strongest
baseline, rescaled to the room that baseline left:

```
skill = (accuracy - baseline) / (100 - baseline) * 100
```

Negative skill means a one-line rule beat the pipeline on the same rows. That is
the most useful number on the scorecard.

## Balanced accuracy

`balanced_accuracy_pct` is the mean of up-recall and down-recall. Raw accuracy on
an unbalanced sample (here 82% down moves) is dominated by the majority class; a
model with a permanent long bias can look terrible on raw accuracy while still
discriminating direction.

## Reliability

`reliability` bins stated probability against observed outcome and reports, per
bucket, `mean_stated`, `observed_rate_pct`, `n` and `gap_pct`, plus `brier_score`,
`log_loss` and `ece`.

- Decision scorecard: `source = decision_confidence` (integer confidence / 100).
- Forecast scorecard: `source = forecast_probability_midpoint`
  (midpoint of `probability_low` / `probability_high`).

A bucket where `mean_stated` is 0.31 and `observed_rate_pct` is 62 means the
confidence field is moving the wrong way. That is a finding, not a bug in the
table.

## Scorecard fields

| Field | Meaning |
|---|---|
| `direction_accuracy_pct` | banded accuracy, neutral outcomes excluded |
| `legacy_direction_accuracy_pct` | the previous sign-based accuracy, kept for comparison |
| `legacy_directional_records` | size of the sign-based sample |
| `balanced_accuracy_pct` | mean of up-recall and down-recall |
| `base_rate_up_pct` | share of up moves in the scored sample |
| `neutral_rate_pct` | share of settled outcomes inside the band |
| `measurement` | labelling scheme, band size, sample window, assets, per-asset regime |
| `baselines` | the four naive rules, plus model skill against the best of them |
| `reliability` | calibration table for stated confidence |

Stored history is not rewritten. Rows settled before this change keep their
original `direction_correct`; the banded value is computed alongside and reported
as the primary figure, with the old one preserved.

## Commands

```bash
# Recompute everything on committed data without touching the live ledgers
python -m intelligence_v2.measure --history-dir history

# Sweep the noise band instead of accepting the default k
python -m intelligence_v2.measure --history-dir history --band-k 0.75

# Unit tests
python -m unittest discover -s tests -p 'test_*.py'
```

`measure` writes `history/intelligence_v2/measurement_report.json` and prints the
summary. It is read-only with respect to decision, evaluation and forecast
ledgers.

## Known limits

- Overlapping horizons mean rows are not independent; `measurement.effective_sample_note`
  says so on every scorecard.
- A single regime still produces a single answer. Comparing across regimes
  requires `measurement.regime` to differ between samples.
- `walkforward`, `replay` and `robustness` scorecards do not attach baselines:
  their evaluations run against temporary history directories, so the baseline
  series is not the one the evaluator used.
- The promotion gate reads `direction_accuracy_pct`, so it now evaluates the
  banded metric. Its 55% threshold has not been recalibrated.
