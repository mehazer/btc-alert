"""
agents/mechanism_interpreter.py
--------------------------------
Interprets the relationship between price action, open interest (OI),
volume, funding, and BTC-relative performance.

Infers likely market mechanism (fresh longs, short covering, etc.),
compares the current move to a previous comparable spike, and scores
sudden-drop risk.

Design principles:
- Deterministic rule-based logic (no LLM)
- Never treat OI alone as bullish/bearish
- Clearly label data limitations (venue, contract type, missing fields)
- Output structured analysis, not trading instructions
"""

from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LOOKBACK_CANDLES = 60
MIN_SPIKE_PCT = 3.0          # minimum % move to count as a spike
OI_SHARP_PCT = 5.0           # OI change above this = "sharp"
OI_GRADUAL_PCT = 1.5         # OI change below this (when rising) = gradual
VOL_CLIMAX_MULT = 2.0        # volume vs recent avg
VOL_HIGH_MULT = 1.35
FUNDING_STRONG = 0.0003      # |funding| threshold for "strong"


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return ((a - b) / b) * 100


def _dir_label(change_pct, flat_band=0.3):
    if change_pct is None:
        return "Unknown"
    if change_pct > flat_band:
        return "Rising"
    if change_pct < -flat_band:
        return "Falling"
    return "Flat"


def _avg(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _classify_volume(last_vol, avg_vol):
    if last_vol is None or avg_vol is None or avg_vol == 0:
        return "Unknown", None
    ratio = last_vol / avg_vol
    if ratio >= VOL_CLIMAX_MULT:
        return "Climactic", ratio
    if ratio >= VOL_HIGH_MULT:
        return "Increasing", ratio
    if ratio <= 0.7:
        return "Decreasing", ratio
    return "Normal", ratio


def _funding_label(rate):
    if rate is None:
        return "Unknown"
    if rate >= FUNDING_STRONG:
        return "Strongly Positive"
    if rate >= 0.00005:
        return "Positive"
    if rate <= -FUNDING_STRONG:
        return "Strongly Negative"
    if rate <= -0.00005:
        return "Negative"
    return "Neutral"

# (content truncated in this simulation - in real call use full file)
