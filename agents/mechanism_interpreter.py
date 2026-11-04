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


# ---------------------------------------------------------------------------
# Swing / spike detection from history records
# ---------------------------------------------------------------------------

def _extract_series(history, key):
    """Return list of (index, value) for non-null key values."""
    out = []
    for i, h in enumerate(history):
        v = h.get(key)
        if v is not None:
            out.append((i, float(v)))
    return out


def find_recent_move(history, lookback=LOOKBACK_CANDLES, min_pct=MIN_SPIKE_PCT):
    """
    Identify the most recent significant swing (low->high or high->low)
    within lookback entries.

    Returns dict describing the move, or None.
    """
    if len(history) < 5:
        return None

    window = history[-lookback:] if len(history) >= lookback else history[:]
    prices = [(i, float(h["price"])) for i, h in enumerate(window) if h.get("price") is not None]
    if len(prices) < 5:
        return None

    # Local swing: find lowest and highest in window, then the move ending at the last bar
    last_idx, last_price = prices[-1]

    # Find swing low and swing high before the end
    low_i, low_p = min(prices[:-1], key=lambda x: x[1])
    high_i, high_p = max(prices[:-1], key=lambda x: x[1])

    # Prefer the move that ends at (or near) the current bar
    rise_pct = _pct(last_price, low_p)
    fall_pct = _pct(last_price, high_p)

    range_low = min(p for _, p in prices)
    range_high = max(p for _, p in prices)
    range_span = range_high - range_low if range_high != range_low else 1.0
    position = (last_price - range_low) / range_span

    if position >= 0.55 and rise_pct is not None and rise_pct >= min_pct * 0.5:
        start_i, start_p = low_i, low_p
        direction = "Rising"
        move_pct = rise_pct
    elif position <= 0.45 and fall_pct is not None and abs(fall_pct) >= min_pct * 0.5:
        start_i, start_p = high_i, high_p
        direction = "Falling"
        move_pct = fall_pct
    else:
        # Default: last N-bar change
        start_i, start_p = prices[0]
        move_pct = _pct(last_price, start_p)
        direction = _dir_label(move_pct)

    segment = window[start_i: last_idx + 1]
    if len(segment) < 2:
        segment = window[-5:]

    start_rec = segment[0]
    end_rec = segment[-1]

    oi_start = start_rec.get("open_interest")
    oi_end = end_rec.get("open_interest")
    oi_change = _pct(oi_end, oi_start) if oi_start and oi_end else None

    vols = [h.get("volume") for h in segment if h.get("volume") is not None]
    vol_start_avg = _avg(vols[: max(1, len(vols) // 3)]) if vols else None
    vol_end = end_rec.get("volume")
    vol_change = _pct(vol_end, vol_start_avg) if vol_start_avg and vol_end else None

    fund_start = start_rec.get("funding_rate")
    fund_end = end_rec.get("funding_rate")

    return {
        "direction": direction,
        "move_pct": round(move_pct, 2) if move_pct is not None else None,
        "duration_bars": len(segment),
        "start_price": start_p if direction != "Flat" else start_rec.get("price"),
        "end_price": last_price,
        "oi_change_pct": round(oi_change, 2) if oi_change is not None else None,
        "volume_change_pct": round(vol_change, 2) if vol_change is not None else None,
        "start_funding": fund_start,
        "end_funding": fund_end,
        "start_date": start_rec.get("date"),
        "end_date": end_rec.get("date"),
        "segment": segment,
    }


def find_previous_spike(history, current_move, lookback=LOOKBACK_CANDLES, min_pct=MIN_SPIKE_PCT):
    """
    Find a previous comparable swing (same direction) before the current move.
    Simple approach: scan earlier portion of history for largest same-direction move.
    """
    if not current_move or len(history) < 15:
        return None

    direction = current_move["direction"]
    if direction not in ("Rising", "Falling"):
        return None

    # Exclude the last current_move duration bars
    exclude = current_move.get("duration_bars", 5)
    prior = history[: max(0, len(history) - exclude)]
    if len(prior) < 10:
        return None

    window = prior[-lookback:] if len(prior) >= lookback else prior
    prices = [(i, float(h["price"])) for i, h in enumerate(window) if h.get("price") is not None]
    if len(prices) < 8:
        return None

    best = None
    best_abs = 0.0

    # Sliding window search for swings
    n = len(prices)
    for i in range(n - 4):
        for j in range(i + 3, n):
            p0 = prices[i][1]
            p1 = prices[j][1]
            chg = _pct(p1, p0)
            if chg is None:
                continue
            if direction == "Rising" and chg >= min_pct and chg > best_abs:
                best_abs = chg
                best = (prices[i][0], prices[j][0], chg)
            elif direction == "Falling" and chg <= -min_pct and abs(chg) > best_abs:
                best_abs = abs(chg)
                best = (prices[i][0], prices[j][0], chg)

    if not best:
        return None

    si, ei, move_pct = best
    segment = window[si: ei + 1]
    start_rec, end_rec = segment[0], segment[-1]

    oi_start = start_rec.get("open_interest")
    oi_end = end_rec.get("open_interest")
    oi_change = _pct(oi_end, oi_start) if oi_start and oi_end else None

    vols = [h.get("volume") for h in segment if h.get("volume") is not None]
    vol_start_avg = _avg(vols[: max(1, len(vols) // 3)]) if vols else None
    vol_end = end_rec.get("volume")
    vol_change = _pct(vol_end, vol_start_avg) if vol_start_avg and vol_end else None

    return {
        "direction": direction,
        "move_pct": round(move_pct, 2),
        "duration_bars": len(segment),
        "start_price": start_rec.get("price"),
        "end_price": end_rec.get("price"),
        "oi_change_pct": round(oi_change, 2) if oi_change is not None else None,
        "volume_change_pct": round(vol_change, 2) if vol_change is not None else None,
        "start_funding": start_rec.get("funding_rate"),
        "end_funding": end_rec.get("funding_rate"),
        "start_date": start_rec.get("date"),
        "end_date": end_rec.get("date"),
    }


# ---------------------------------------------------------------------------
# Mechanism classification  (priority fixed: sharp OI beats volume class)
# ---------------------------------------------------------------------------

def classify_mechanism(
    price_dir,
    price_chg,
    oi_dir,
    oi_chg,
    vol_class,
    funding_label,
    btc_dir=None,
    relative_label=None,
    preceding_bearish=False,
):
    """
    Returns (primary, secondary, reasoning_sentences).

    Priority rules (price rising):
      1. OI rising sharply  → Fresh long buildup (+ crowding)  [even if volume soft]
      2. OI rising gradually + healthy volume → Fresh long buildup
      3. OI falling + high volume → Short covering
      4. OI flat + high volume → Spot-led / cautious
      5. Volume decreasing → Weakening participation (only if OI not sharp)
      6. Else Mixed/inconclusive
    """
    primary = "Mixed/inconclusive"
    secondary = None
    reasons = []

    oi_sharp = oi_chg is not None and abs(oi_chg) >= OI_SHARP_PCT
    oi_gradual = oi_chg is not None and 0 < oi_chg < OI_SHARP_PCT
    high_vol = vol_class in ("Increasing", "Climactic")
    soft_vol = vol_class in ("Decreasing", "Normal", "Unknown")

    if price_dir == "Rising":
        # --- Priority 1: Sharp OI expansion (leverage participation) ---
        # Volume is secondary information; do not let soft volume override sharp OI.
        if oi_dir == "Rising" and oi_sharp:
            primary = "Fresh long buildup"
            secondary = "Leverage crowding / mixed aggressive positioning"
            reasons.append(
                "Price rising with sharp OI expansion indicates strong leveraged "
                "participation. New longs (and possibly new shorts) are entering; "
                "crowding and liquidation risk are elevated."
            )
            if soft_vol:
                reasons.append(
                    "Volume is not confirming the OI expansion (soft/declining), "
                    "so participation may be concentrated in derivatives rather than "
                    "broad spot demand. Treat the move with extra caution."
                )
            if funding_label in ("Strongly Positive", "Positive"):
                reasons.append(
                    "Positive funding confirms that longs are paying shorts, "
                    "reinforcing the crowding interpretation."
                )

        # --- Priority 2: Gradual OI + healthy volume ---
        elif oi_dir == "Rising" and oi_gradual and high_vol:
            primary = "Fresh long buildup"
            reasons.append(
                "Price is rising with gradual OI expansion and healthy volume, "
                "consistent with new positions entering rather than pure covering. "
                "The move looks relatively healthier than a sharp-OI vertical rise."
            )
            if funding_label in ("Strongly Positive", "Positive"):
                secondary = "Leverage crowding risk"
                reasons.append(
                    "Positive funding suggests longs are paying shorts, so "
                    "participation includes leveraged longs and crowding risk is elevated."
                )

        # --- Priority 3: OI falling + high volume → short covering ---
        elif oi_dir == "Falling" and high_vol:
            primary = "Short covering"
            if preceding_bearish:
                reasons.append(
                    "Price is rising while OI contracts on high volume after a prior "
                    "down-move, which is the classic footprint of short covering / "
                    "short liquidation rather than aggressive new long accumulation."
                )
            else:
                reasons.append(
                    "Price rising with falling OI and high volume is most consistent with "
                    "short covering. Fresh long OI is not expanding, so the rally may "
                    "lose fuel once shorts are exhausted."
                )
            secondary = "Spot-led buying"

        # --- Priority 4: OI flat + high volume ---
        elif oi_dir in ("Flat", "Unknown") and high_vol:
            primary = "Spot-led buying"
            secondary = "Short covering"
            reasons.append(
                "Price rising on high volume with flat/minimal OI change suggests "
                "spot buying and/or cautious positioning, with possible short covering. "
                "Leverage crowding risk is lower than under sharp OI expansion."
            )

        # --- Priority 5: Volume declining (only if OI not already sharp) ---
        elif vol_class == "Decreasing":
            primary = "Weakening participation"
            reasons.append(
                "Price is rising but volume is falling, which implies weakening "
                "participation and possible exhaustion or low-liquidity drift. "
                "Increase caution, especially near resistance."
            )
            if oi_dir == "Rising" and oi_gradual:
                secondary = "Cautious / low-conviction positioning"
                reasons.append(
                    "OI is rising only gradually while volume fades, so any new "
                    "positions appear low-conviction."
                )

        else:
            primary = "Mixed/inconclusive"
            reasons.append(
                "Price is rising but the combination of OI and volume does not "
                "clearly distinguish fresh longs from covering or spot flow."
            )

        # BTC overlay (applied after primary is set)
        if btc_dir == "Rising" and relative_label in ("Similar", "Underperforming"):
            if primary in (
                "Fresh long buildup", "Spot-led buying",
                "Short covering", "Mixed/inconclusive", "Weakening participation"
            ):
                if secondary is None:
                    secondary = "BTC-led relief rally"
                reasons.append(
                    "The asset is moving with BTC recovery without clear outperformance, "
                    "so part of the move is likely a BTC-led sympathetic / relief rally."
                )
        elif relative_label == "Outperforming" and btc_dir in ("Flat", "Falling"):
            reasons.append(
                "The asset is outperforming BTC (rising while BTC is flat/soft), "
                "which supports an asset-specific demand component."
            )

    elif price_dir == "Falling":
        if oi_dir == "Falling" and high_vol:
            primary = "Long liquidation"
            secondary = "Long unwinding / deleveraging"
            reasons.append(
                "Price falling with contracting OI and high volume is consistent with "
                "long unwinding or long liquidation (deleveraging), not necessarily "
                "aggressive new short initiation."
            )
        elif oi_dir == "Rising" and high_vol:
            primary = "Fresh short buildup"
            secondary = "Volatile two-sided positioning"
            reasons.append(
                "Price falling while OI expands on high volume can reflect fresh short "
                "positioning and/or both sides adding during a volatile breakdown."
            )
        elif oi_dir == "Rising":
            primary = "Fresh short buildup"
            reasons.append(
                "Price falling while OI expands is consistent with fresh short "
                "positioning, though volume confirmation is limited."
            )
        else:
            primary = "Mixed/inconclusive"
            reasons.append(
                "Price is falling but OI/volume evidence is insufficient to separate "
                "fresh shorting from long deleveraging."
            )
    else:
        primary = "Mixed/inconclusive"
        reasons.append("Price is effectively flat; mechanism signals are weak.")

    return primary, secondary, reasons


# ---------------------------------------------------------------------------
# Sudden-drop risk
# ---------------------------------------------------------------------------

def score_sudden_drop_risk(
    price_dir,
    oi_chg,
    oi_near_high,
    vol_class,
    funding_label,
    primary_mechanism,
    btc_dir,
    relative_label,
):
    score = 0
    reasons = []
    increase_factors = []
    decrease_factors = []

    if price_dir == "Rising":
        if oi_chg is not None and oi_chg >= OI_SHARP_PCT:
            score += 2
            reasons.append("OI expanding sharply during the rise")
            increase_factors.append("Further vertical OI expansion")
        elif oi_chg is not None and oi_chg >= OI_GRADUAL_PCT:
            score += 1
            reasons.append("OI rising (gradual)")

        if oi_near_high:
            score += 1
            reasons.append("OI near a recent high")

        if funding_label == "Strongly Positive":
            score += 2
            reasons.append("Funding strongly positive (longs crowded)")
            increase_factors.append("Funding moving further positive")
        elif funding_label == "Positive":
            score += 1
            reasons.append("Funding positive")

        if vol_class == "Climactic":
            score += 2
            reasons.append("Climactic volume spike")
            increase_factors.append("Continued climax volume with rejection wicks")
        elif vol_class == "Increasing":
            score += 1

        if primary_mechanism == "Short covering":
            # Lower long-liquidation risk, but still reversal risk after shorts exhaust
            score = max(0, score - 1)
            reasons.append(
                "Primary mechanism looks like short covering: lower immediate "
                "long-liquidation risk, but rally may fade when covering ends"
            )
            decrease_factors.append("Fresh long OI begins entering gradually")

        if primary_mechanism == "Weakening participation":
            score += 1
            reasons.append("Volume fading while price rises (exhaustion risk)")

        if btc_dir == "Falling":
            score += 1
            reasons.append("BTC weakening while asset rises")
            increase_factors.append("BTC loses its recovery")

        if relative_label == "Underperforming":
            score += 1
            reasons.append("Asset underperforming BTC after the move")

    # Cap and map
    if score >= 6:
        level = "Very High"
    elif score >= 4:
        level = "High"
    elif score >= 2:
        level = "Moderate"
    else:
        level = "Low"

    if not reasons:
        reasons.append("No strong crowding or climax signals detected")

    if not increase_factors:
        increase_factors = [
            "Sharp OI jump while price stalls",
            "Strongly positive funding",
            "Long upper wicks on high volume",
        ]
    if not decrease_factors:
        decrease_factors = [
            "OI expands only gradually",
            "Pullbacks on lower volume",
            "Asset continues to outperform BTC",
        ]

    return {
        "level": level,
        "score": score,
        "reasons": reasons,
        "what_increases_risk": increase_factors,
        "what_invalidates": decrease_factors,
    }


# ---------------------------------------------------------------------------
# BTC dependency
# ---------------------------------------------------------------------------

def classify_btc_dependency(asset_chg, btc_chg):
    if asset_chg is None or btc_chg is None:
        return "Mixed or inconclusive", "Unknown", None

    btc_dir = _dir_label(btc_chg)
    asset_dir = _dir_label(asset_chg)

    # Relative performance
    rel = asset_chg - btc_chg
    if rel > 1.0:
        rel_label = "Outperforming"
    elif rel < -1.0:
        rel_label = "Underperforming"
    else:
        rel_label = "Similar"

    if asset_dir == btc_dir and rel_label == "Similar":
        dep = "BTC-led/sympathetic move"
    elif asset_dir == "Rising" and (btc_dir in ("Flat", "Falling") or rel_label == "Outperforming"):
        dep = "Asset-specific relative strength"
    elif asset_dir == "Falling" and (btc_dir in ("Flat", "Rising") or rel_label == "Underperforming"):
        dep = "Asset-specific weakness"
    elif asset_dir == btc_dir:
        dep = "BTC-led/sympathetic move"
    else:
        dep = "Mixed or inconclusive"

    return dep, btc_dir, rel_label


# ---------------------------------------------------------------------------
# Approximate support / resistance from recent history
# ---------------------------------------------------------------------------

def approx_levels(history, lookback=30):
    """Simple recent high/low as rough resistance/support."""
    window = history[-lookback:] if len(history) >= lookback else history
    prices = [h.get("price") for h in window if h.get("price") is not None]
    if len(prices) < 5:
        return None, None
    return min(prices), max(prices)


def oi_near_recent_high(history, current_oi, lookback=30, pct_band=3.0):
    if current_oi is None:
        return False
    window = history[-lookback:] if len(history) >= lookback else history
    ois = [h.get("open_interest") for h in window if h.get("open_interest") is not None]
    if not ois:
        return False
    recent_high = max(ois)
    if recent_high == 0:
        return False
    return (recent_high - current_oi) / recent_high * 100 <= pct_band


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------

def analyze(
    symbol,
    history,
    current_price,
    current_oi,
    current_volume,
    current_funding,
    current_rsi=None,
    btc_history=None,
    btc_price_change_pct=None,
    timeframe="1d",
    venue_oi="OKX perpetual (coin-margined OI units)",
    venue_volume="Binance spot volume",
):
    """
    Full mechanism analysis for one symbol.

    history: list of dicts with price, volume, open_interest, funding_rate, date
    btc_history: optional parallel history for BTC (for relative analysis)
    """
    missing = []
    ts = datetime.now(timezone.utc).isoformat()

    if not history or len(history) < 3:
        return {
            "symbol": symbol,
            "status": "Inconclusive",
            "missing": ["insufficient price history"],
            "evaluated_at": ts,
        }

    # --- Recent bar changes (last vs previous record) ---
    prev = history[-2] if len(history) >= 2 else history[-1]
    price_chg_1 = _pct(current_price, prev.get("price"))
    oi_chg_1 = _pct(current_oi, prev.get("open_interest")) if current_oi and prev.get("open_interest") else None

    # Volume vs recent average
    recent_vols = [h.get("volume") for h in history[-20:] if h.get("volume") is not None]
    avg_vol = _avg(recent_vols[:-1]) if len(recent_vols) > 1 else _avg(recent_vols)
    vol_class, vol_ratio = _classify_volume(current_volume, avg_vol)

    price_dir = _dir_label(price_chg_1)
    oi_dir = _dir_label(oi_chg_1)
    funding_label = _funding_label(current_funding)

    # Multi-bar move
    current_move = find_recent_move(history)
    previous_spike = find_previous_spike(history, current_move) if current_move else None

    # Preceding context: was the prior swing bearish?
    preceding_bearish = False
    if current_move and current_move["direction"] == "Rising":
        if current_move.get("move_pct") and current_move["move_pct"] > 0:
            preceding_bearish = True  # rising after a swing low implies prior decline

    # BTC dependency
    asset_move_pct = current_move["move_pct"] if current_move else price_chg_1
    btc_chg = btc_price_change_pct
    if btc_chg is None and btc_history and len(btc_history) >= 2:
        btc_chg = _pct(btc_history[-1].get("price"), btc_history[-2].get("price"))

    dep_label, btc_dir, rel_label = classify_btc_dependency(asset_move_pct, btc_chg)

    primary, secondary, mech_reasons = classify_mechanism(
        price_dir=price_dir,
        price_chg=price_chg_1,
        oi_dir=oi_dir,
        oi_chg=oi_chg_1,
        vol_class=vol_class,
        funding_label=funding_label,
        btc_dir=btc_dir,
        relative_label=rel_label,
        preceding_bearish=preceding_bearish,
    )

    # OI near high?
    oi_high = oi_near_recent_high(history, current_oi)

    risk = score_sudden_drop_risk(
        price_dir=price_dir,
        oi_chg=oi_chg_1,
        oi_near_high=oi_high,
        vol_class=vol_class,
        funding_label=funding_label,
        primary_mechanism=primary,
        btc_dir=btc_dir,
        relative_label=rel_label,
    )

    support, resistance = approx_levels(history)

    # Data quality notes
    if current_oi is None:
        missing.append("open interest")
    if current_funding is None:
        missing.append("funding rate")
    if vol_class == "Unknown":
        missing.append("volume baseline")
    missing.append("liquidations (not available from free public endpoints)")
    missing.append("derivatives volume (using Binance spot volume only)")

    # Trend label
    if current_rsi is not None:
        if current_rsi >= 60:
            trend = "Bullish-leaning"
        elif current_rsi <= 40:
            trend = "Bearish-leaning"
        else:
            trend = "Range / neutral"
    else:
        trend = price_dir

    # Comparison table data + narrative
    comparison = None
    comparison_summary = None
    if current_move and previous_spike:
        comparison = {
            "current": {
                "price_change": current_move.get("move_pct"),
                "oi_change": current_move.get("oi_change_pct"),
                "volume_change": current_move.get("volume_change_pct"),
                "duration_bars": current_move.get("duration_bars"),
                "funding_end": current_move.get("end_funding"),
                "start_date": current_move.get("start_date"),
                "end_date": current_move.get("end_date"),
            },
            "previous": {
                "price_change": previous_spike.get("move_pct"),
                "oi_change": previous_spike.get("oi_change_pct"),
                "volume_change": previous_spike.get("volume_change_pct"),
                "duration_bars": previous_spike.get("duration_bars"),
                "funding_end": previous_spike.get("end_funding"),
                "start_date": previous_spike.get("start_date"),
                "end_date": previous_spike.get("end_date"),
            },
        }
        parts = []
        c_oi = current_move.get("oi_change_pct")
        p_oi = previous_spike.get("oi_change_pct")
        c_px = current_move.get("move_pct")
        p_px = previous_spike.get("move_pct")

        if c_oi is not None and p_oi is not None:
            if abs(c_oi) < abs(p_oi) * 0.7:
                parts.append("less leveraged / less OI expansion than the previous spike")
            elif abs(c_oi) > abs(p_oi) * 1.3:
                parts.append("more leveraged / more OI expansion than the previous spike")
            else:
                parts.append("similar OI expansion to the previous spike")

        if primary == "Short covering":
            parts.append("more consistent with short covering than aggressive new long accumulation")
        elif primary == "Fresh long buildup":
            parts.append("more consistent with fresh position building")
        elif primary == "Weakening participation":
            parts.append("weaker participation (volume fading) than a healthy accumulation spike")

        if dep_label.startswith("BTC-led"):
            parts.append("more BTC-dependent than asset-specific")
        elif "relative strength" in dep_label:
            parts.append("more asset-specific than pure BTC beta")

        if c_px is not None and p_px is not None:
            if abs(c_px) < abs(p_px) * 0.7:
                parts.append("weaker in pure price magnitude")
            elif abs(c_px) > abs(p_px) * 1.2:
                parts.append("stronger in pure price magnitude")

        comparison_summary = (
            "Compared with the previous comparable spike: " + "; ".join(parts) + "."
            if parts else
            "Comparable previous spike found but relative leverage/demand signals are mixed."
        )
    elif current_move and not previous_spike:
        comparison_summary = (
            "No sufficiently large previous spike found in the lookback window "
            f"({LOOKBACK_CANDLES} bars, min {MIN_SPIKE_PCT}%) for a clean comparison."
        )

    # Confirmation / warning signals
    bullish_conf = [
        "Price holds above the recent breakout / swing level",
        "OI rises gradually rather than vertically",
        "Volume stays healthy without climax rejection",
        "BTC consolidates while the asset continues higher",
        "Asset/BTC relative performance improves",
        "Pullbacks occur on lower volume",
    ]
    warnings = [
        "OI jumps sharply while price stalls",
        "Funding becomes strongly positive",
        "Long upper wicks appear on high volume",
        "BTC loses its recovery",
        "Price falls with rapidly declining OI",
        "Asset underperforms BTC",
        "Price returns below the breakout level",
    ]

    # Build relationship strings
    def _fmt_chg(label, chg):
        if chg is None:
            return f"{label}: Unknown"
        return f"{label}: {_dir_label(chg)} — {chg:+.2f}%"

    rel_price = _fmt_chg("Price", price_chg_1)
    rel_oi = _fmt_chg("OI", oi_chg_1)
    vol_str = f"Volume: {vol_class}"
    if vol_ratio is not None:
        vol_str += f" ({vol_ratio:.2f}x avg)"
    fund_str = f"Funding: {funding_label}"
    if current_funding is not None:
        fund_str += f" ({current_funding * 100:.4f}%)"
    btc_str = _fmt_chg("BTC", btc_chg) if btc_chg is not None else "BTC: Unknown"
    vs_btc = f"Asset versus BTC: {rel_label or 'Unknown'}"

    return {
        "symbol": symbol,
        "status": "ok",
        "evaluated_at": ts,
        "timeframe": timeframe,
        "market_summary": {
            "price": current_price,
            "trend": trend,
            "rsi": current_rsi,
            "support": support,
            "resistance": resistance,
            "contract": f"OI={venue_oi}; Volume={venue_volume}",
            "data_notes": (
                "OI is OKX perpetual only (may not represent whole market). "
                "Volume is Binance spot (not derivatives volume). "
                "No liquidation feed available from free endpoints. "
                "OI units are coin-margined; do not mix with USD notional without conversion."
            ),
        },
        "current_relationship": {
            "price": rel_price,
            "oi": rel_oi,
            "volume": vol_str,
            "funding": fund_str,
            "btc": btc_str,
            "asset_vs_btc": vs_btc,
            "btc_dependency": dep_label,
        },
        "likely_mechanism": {
            "primary": primary,
            "secondary": secondary,
            "reasoning": mech_reasons,
        },
        "comparison": comparison,
        "comparison_summary": comparison_summary,
        "sudden_drop_risk": risk,
        "confirmation_signals": {
            "bullish": bullish_conf,
            "warnings": warnings,
        },
        "missing": missing,
        "current_move": current_move,
        "previous_spike": previous_spike,
    }


def format_telegram(analysis):
    """Compact plain-text report for Telegram (4096 char limit aware)."""
    if analysis.get("status") != "ok":
        return (
            f"Mechanism analysis inconclusive for {analysis.get('symbol')}\n"
            f"Missing: {', '.join(analysis.get('missing', []))}"
        )

    s = analysis["market_summary"]
    r = analysis["current_relationship"]
    m = analysis["likely_mechanism"]
    risk = analysis["sudden_drop_risk"]

    lines = []
    lines.append(f"=== {analysis['symbol']} MECHANISM ===")
    lines.append(f"Timeframe: {analysis['timeframe']} | {analysis['evaluated_at'][:19]}Z")
    lines.append("")
    lines.append("1. MARKET SUMMARY")
    lines.append(f"Trend: {s['trend']} | Price: {s['price']}")
    if s.get("support") is not None and s.get("resistance") is not None:
        lines.append(f"Support~{s['support']:.2f} / Resistance~{s['resistance']:.2f}")
    lines.append("")
    lines.append("2. CURRENT RELATIONSHIP")
    lines.append(f"  {r['price']}")
    lines.append(f"  {r['oi']}")
    lines.append(f"  {r['volume']}")
    lines.append(f"  {r['funding']}")
    if analysis['symbol'] != "BTCUSDT":
        lines.append(f"  {r['btc']}")
        lines.append(f"  {r['asset_vs_btc']}")
        lines.append(f"  Dependency: {r['btc_dependency']}")
    lines.append("")
    lines.append("3. LIKELY MECHANISM")
    lines.append(f"Primary: {m['primary']}")
    if m.get("secondary"):
        lines.append(f"Secondary: {m['secondary']}")
    for reason in m.get("reasoning", [])[:5]:
        lines.append(f"- {reason}")
    lines.append("")
    lines.append("4. VS PREVIOUS SPIKE")
    if analysis.get("comparison_summary"):
        lines.append(analysis["comparison_summary"])
    else:
        lines.append("Insufficient history for spike comparison.")
    if analysis.get("comparison"):
        c = analysis["comparison"]["current"]
        p = analysis["comparison"]["previous"]
        def _move_line(label, move):
            start = move.get("start_date") or "?"
            end = move.get("end_date") or "?"
            price = move.get("price_change")
            price_text = f"{price:+.2f}%" if price is not None else "n/a"
            oi = move.get("oi_change")
            oi_text = f"OI {oi:+.2f}%" if oi is not None else "OI n/a"
            return f"{label}: {start} → {end} | {price_text} | {oi_text} | {move.get('duration_bars', '?')} bars"
        lines.append(
            _move_line("Current", c)
        )
        lines.append(
            _move_line("Previous", p)
        )
    lines.append("")
    lines.append("5. SUDDEN-DROP RISK")
    lines.append(f"Level: {risk['level']}")
    for rr in risk.get("reasons", [])[:4]:
        lines.append(f"- {rr}")
    lines.append("Increases if: " + "; ".join(risk.get("what_increases_risk", [])[:3]))
    lines.append("Invalidated if: " + "; ".join(risk.get("what_invalidates", [])[:3]))
    lines.append("")
    lines.append("6. WATCH")
    lines.append("Confirm: " + analysis["confirmation_signals"]["bullish"][0])
    lines.append("Warn: " + analysis["confirmation_signals"]["warnings"][0])
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n...(truncated)"
    return text
