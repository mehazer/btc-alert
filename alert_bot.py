"""
Self-contained multi-coin alert bot.
Real data (Binance + OKX + F&G) + rich Telegram cards with figures.
Persists open_interest into history/SYMBOL.json for intelligence_v2.
"""
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()]
PRIMARY = "BTCUSDT"
INTERVAL = os.environ.get("INTERVAL", "4h")
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", "14"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BINANCE_KLINES = "https://data-api.binance.vision/api/v3/klines"
OKX_OI = "https://www.okx.com/api/v5/public/open-interest"
OKX_FUNDING = "https://www.okx.com/api/v5/public/funding-rate"
FEAR_GREED = "https://api.alternative.me/fng/"

OKX_MAP = {
    "BTCUSDT": "BTC-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
    "SOLUSDT": "SOL-USDT-SWAP",
    "ZECUSDT": "ZEC-USDT-SWAP",
    "ZAMAUSDT": "ZAMA-USDT-SWAP",
}

HISTORY_DIR = Path("history")
HISTORY_DIR.mkdir(exist_ok=True)


def send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets missing")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=30)
        print("Telegram status:", r.status_code)
        return r.ok
    except Exception as e:
        print("Telegram error:", e)
        return False


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b * 100


def fetch_binance(symbol, limit=80, include_partial=False):
    r = requests.get(BINANCE_KLINES, params={"symbol": symbol, "interval": INTERVAL, "limit": limit}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    history, closes = [], []
    captured_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for row in rows:
        bar_close_ts = int(row[6])
        # Binance includes the currently forming candle. Never mix its partial
        # OHLCV into closed-bar indicators or prediction features.
        bar_complete = captured_at_ms > bar_close_ts
        if not bar_complete and not include_partial:
            continue
        close = float(row[4])
        vol = float(row[5])
        closes.append(close)
        history.append({
            "price": close,
            "volume": vol,
            "oi": None,
            "funding": None,
            "ts": int(row[0]),
            "bar_open_ts": int(row[0]),
            "bar_close_ts": bar_close_ts,
            "captured_at_ms": captured_at_ms,
            "bar_complete": bar_complete,
        })
    return history, closes


def fetch_okx(symbol):
    inst = OKX_MAP.get(symbol)
    if not inst:
        return None, None
    oi = funding = None
    try:
        r = requests.get(OKX_OI, params={"instId": inst}, timeout=15)
        if r.ok and r.json().get("data"):
            raw_oi = r.json()["data"][0].get("oi")
            oi = float(raw_oi) if raw_oi not in (None, "") else None
    except Exception as e:
        print("OI", symbol, e)
    try:
        r = requests.get(OKX_FUNDING, params={"instId": inst}, timeout=15)
        if r.ok and r.json().get("data"):
            raw_funding = r.json()["data"][0].get("fundingRate")
            funding = float(raw_funding) if raw_funding not in (None, "") else None
    except Exception as e:
        print("Funding", symbol, e)
    return oi, funding


def fetch_fg():
    try:
        r = requests.get(FEAR_GREED, params={"limit": 1}, timeout=15)
        if r.ok:
            d = (r.json().get("data") or [{}])[0]
            return int(d.get("value", 0)), d.get("value_classification", "Unknown")
    except Exception as e:
        print("F&G", e)
    return None, None


def support_resistance(history, lookback=30):
    """Prior support/resistance: min/max of recent closes excluding the current bar."""
    if len(history) < 6:
        return None, None
    window = history[-(lookback + 1):-1] if len(history) > lookback else history[:-1]
    prices = [h["price"] for h in window if h.get("price") is not None]
    if len(prices) < 5:
        return None, None
    return min(prices), max(prices)


def classify(price_chg, oi_chg, vol_ratio, funding):
    price_up = price_chg is not None and price_chg > 0.3
    price_dn = price_chg is not None and price_chg < -0.3
    oi_up = oi_chg is not None and oi_chg > 1.5
    oi_dn = oi_chg is not None and oi_chg < -1.5
    oi_sharp = oi_chg is not None and abs(oi_chg) >= 5.0
    high_vol = vol_ratio is not None and vol_ratio >= 1.35

    if price_up and oi_up and oi_sharp:
        return "Fresh long buildup", "Leverage crowding risk", "High" if (funding or 0) > 0.0001 else "Moderate"
    if price_up and oi_up:
        return "Fresh long buildup", "Cautious long bias", "Moderate" if high_vol else "Low"
    if price_up and oi_dn and high_vol:
        return "Short covering", "Weak rally risk", "Moderate"
    if price_up and oi_dn:
        return "Short covering", None, "Low"
    if price_up and high_vol:
        return "Spot-led buying", None, "Low"
    if price_up:
        return "Weakening participation", None, "Moderate"
    if price_dn and oi_dn and high_vol:
        return "Long liquidation", "Deleveraging", "High"
    if price_dn and oi_up:
        return "Fresh short buildup", None, "Moderate"
    if price_dn:
        return "Soft selling / drift", None, "Low"
    return "Range / no clear mechanism", None, "Low"


def _read_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _rate_text(row):
    if not isinstance(row, dict) or row.get("accuracy_pct") is None:
        n = (row.get("n") or 0) if isinstance(row, dict) else 0
        return f"n/a (n={n})"
    return f"{float(row['accuracy_pct']):.1f}% (n={int(row.get('n') or 0)})"


def prediction_history_lines(history_dir=None):
    """Return a compact, honest summary of the latest persisted shadow results."""
    history_dir = Path(history_dir or HISTORY_DIR)
    v2_dir = history_dir / "intelligence_v2"
    scorecard = _read_json_file(v2_dir / "forecast_scorecard.json", {})
    def horizon_text(bars, label):
        result = (scorecard.get("by_horizon") or {}).get(bars) or {}
        bases = (scorecard.get("benchmarks_by_horizon") or {}).get(bars) or {}
        if not result:
            return f"{label} n/a"
        n = int(result.get("directional") or 0)
        hit_pct = result.get("accuracy_pct")
        hits = int(result.get("hits") or 0)
        model = f"{float(hit_pct):.0f}% ({hits}/{n})" if hit_pct is not None else f"n/a (0/{n})"
        baseline_text = []
        for key, short in (("always_up", "U"), ("always_down", "D"), ("persistence", "P")):
            base = bases.get(key) or {}
            rate = base.get("accuracy_pct")
            base_n = int(base.get("n") or 0)
            value = f"{float(rate):.0f}%" if rate is not None else "—"
            if base_n != n or rate is None:
                value += f"({base_n})"
            baseline_text.append(f"{short}{value}")
        return f"{label} {model} vs {'/'.join(baseline_text)}"

    abstain = scorecard.get("abstain_rate_pct")
    abstain_text = f"abstain {float(abstain):.0f}%" if abstain is not None else "abstain n/a"
    lines = [
        f"History · pooled shadow · {abstain_text}: "
        f"{horizon_text('1', '4h')}; {horizon_text('3', '12h')}"
    ]

    predictions = _read_json_file(v2_dir / "trend_volume_candidate_ledger.json", [])
    evaluations = _read_json_file(v2_dir / "trend_volume_candidate_evaluations.json", [])
    predictions = predictions if isinstance(predictions, list) else []
    evaluations = evaluations if isinstance(evaluations, list) else []
    outcomes = [
        row for row in evaluations
        if isinstance(row, dict) and row.get("status") == "settled" and row.get("forward_return_pct") is not None
    ]
    active = [row for row in outcomes if row.get("direction") in {"up", "down"}]
    hits = sum(1 for row in active if row.get("direction_correct") is True)
    fired = sum(1 for row in predictions if isinstance(row, dict) and row.get("direction") in {"up", "down"})
    coverage = f"{100 * fired / len(predictions):.0f}%" if predictions else "n/a"

    candidate_baselines = []
    for name, fixed in (("always_up", "up"), ("always_down", "down"), ("persistence", None)):
        eligible = hits_n = 0
        for row in outcomes:
            try:
                ret = float(row["forward_return_pct"])
            except (KeyError, TypeError, ValueError):
                continue
            direction = fixed or (row.get("baseline_directions") or {}).get(name)
            if direction not in {"up", "down"}:
                continue
            eligible += 1
            hits_n += int((direction == "up" and ret > 0) or (direction == "down" and ret < 0))
        pct_text = f"{100 * hits_n / eligible:.0f}%" if eligible else "—"
        candidate_baselines.append(f"{pct_text}({eligible})" if not eligible else pct_text)

    if active:
        candidate_text = f"{100 * hits / len(active):.0f}% ({hits}/{len(active)}) vs U/D/P {'/'.join(candidate_baselines)}"
        lines.append(f"Trend/volume shadow: {candidate_text}; calls {fired}/{len(predictions)}.")
    else:
        lines.append("Trend/volume: unscored, shadow-only.")
    return lines


def build_card(symbol, history, closes, oi, funding, fg_val, fg_label, btc_chg=None, prev_oi=None):
    close_ts = history[-1].get("bar_close_ts")
    now = (
        datetime.fromtimestamp((int(close_ts) + 1) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        if close_ts is not None else datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    )
    price = history[-1]["price"]
    prev_price = history[-2]["price"] if len(history) >= 2 else price
    price_chg = pct(price, prev_price)

    if prev_oi is None and len(history) >= 2:
        prev_oi = history[-2].get("oi")
    oi_chg = pct(oi, prev_oi) if oi is not None and prev_oi not in (None, 0) else None

    vols = [h["volume"] for h in history[-20:] if h.get("volume")]
    avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1) if len(vols) > 1 else (vols[0] if vols else None)
    vol_ratio = (history[-1]["volume"] / avg_vol) if avg_vol and avg_vol > 0 else None

    rsi_val = rsi(closes, RSI_PERIOD)
    primary, secondary, risk = classify(price_chg, oi_chg, vol_ratio, funding)

    bullish = primary in ("Fresh long buildup", "Spot-led buying", "Short covering")
    bearish = primary in ("Fresh short buildup", "Long liquidation", "Soft selling / drift")
    if bullish:
        stance = "Constructive but elevated risk" if risk == "High" else ("Constructive — stay selective" if risk == "Moderate" else "Constructive watch")
    elif bearish:
        stance = "Defensive" if risk in ("High", "Very High") else "Cautious / defensive bias"
    else:
        stance = "Wait — no clear edge"

    def fnum(x, d=2):
        return f"{x:,.{d}f}" if isinstance(x, (int, float)) else "n/a"

    oi_text = f"{oi_chg:+.2f}%" if oi_chg is not None else "n/a"
    funding_text = f"{funding * 100:+.4f}%" if funding is not None else "n/a"
    volume_text = f"{vol_ratio:.2f}x" if vol_ratio is not None else "n/a"
    price_text = f"{fnum(price)} ({price_chg:+.2f}%)" if price_chg is not None else fnum(price)
    stance_short = "Positive" if stance.startswith("Constructive") else "Cautious" if stance.startswith(("Defensive", "Cautious")) else "Wait"
    lines = [
        f"{symbol} · {INTERVAL} close · {now} UTC",
        f"Price {price_text} | RSI {fnum(rsi_val, 0)} | Vol {volume_text} | OI {oi_text} | Funding {funding_text}",
        f"Read: {primary} | {stance_short} | Risk {risk}",
        *prediction_history_lines(),
    ]
    if symbol != PRIMARY and btc_chg is not None:
        lines[2] += f" | BTC {btc_chg:+.2f}%"
    if symbol == PRIMARY and fg_val is not None:
        lines[2] += f" | F&G {fg_val}"
    return "\n".join(lines)


def load_prev_oi(symbol):
    p = HISTORY_DIR / f"{symbol}_{INTERVAL}_last_oi.json"
    if p.exists():
        try:
            return json.loads(p.read_text()).get("oi")
        except Exception:
            pass
    for name in (f"{symbol}_{INTERVAL}.json", f"{symbol}.json"):
        p2 = HISTORY_DIR / name
        if p2.exists():
            try:
                data = json.loads(p2.read_text())
                for h in reversed(data[-40:]):
                    v = h.get("oi") if h.get("oi") is not None else h.get("open_interest")
                    if v is not None:
                        return v
            except Exception:
                pass
    return None


def save_last_oi(symbol, oi):
    if oi is None:
        return
    p = HISTORY_DIR / f"{symbol}_{INTERVAL}_last_oi.json"
    p.write_text(json.dumps({"oi": oi, "ts": datetime.now(timezone.utc).isoformat()}))


def save_hist(symbol, history):
    p = HISTORY_DIR / f"{symbol}_{INTERVAL}.json"
    p.write_text(json.dumps(history[-200:]))


def update_rich_history(symbol, price, volume, oi, funding, rsi_val, prev_oi, price_chg,
                        bar_open_ts=None, bar_close_ts=None, captured_at_ms=None):
    """Append/update SYMBOL.json used by intelligence_v2 (open_interest field)."""
    p = HISTORY_DIR / f"{symbol}.json"
    rows = []
    if p.exists():
        try:
            rows = json.loads(p.read_text())
            if not isinstance(rows, list):
                rows = []
        except Exception:
            rows = []

    captured_at_ms = captured_at_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    observed_ms = int(bar_close_ts) if bar_close_ts is not None else captured_at_ms
    now = datetime.fromtimestamp(observed_ms / 1000, tz=timezone.utc).isoformat()
    oi_chg = pct(oi, prev_oi) if oi is not None and prev_oi not in (None, 0) else None
    # Upsert only the same candle, not any row that happens to be within a
    # three-hour wall-clock window. This keeps delayed workflow runs aligned.
    update_index = None
    if bar_close_ts is not None:
        for index in range(len(rows) - 1, -1, -1):
            if str(rows[index].get("bar_close_ts")) == str(bar_close_ts):
                update_index = index
                break

    row = {
        "date": now,
        "retrieved_at": datetime.fromtimestamp(captured_at_ms / 1000, tz=timezone.utc).isoformat(),
        "timeframe": INTERVAL,
        "bar_open_ts": int(bar_open_ts) if bar_open_ts is not None else None,
        "bar_close_ts": int(bar_close_ts) if bar_close_ts is not None else None,
        "captured_at_ms": int(captured_at_ms),
        "bar_complete": bar_close_ts is not None,
        "price_venue": "Binance",
        "price_dataset": "spot-klines",
        "price_unit": "USDT per base asset",
        "volume_venue": "Binance",
        "volume_dataset": "spot-klines",
        "volume_unit": "base asset",
        "oi_venue": "OKX",
        "oi_dataset": "perpetual-open-interest",
        "oi_unit": "coin OI",
        "funding_venue": "OKX",
        "funding_dataset": "perpetual-funding-rate",
        "funding_unit": "rate per interval",
        "rsi": rsi_val,
        "price": price,
        "volume": volume,
        "open_interest": oi,
        "oi": oi,
        "funding_rate": funding,
        "funding": funding,
        "oi_change_pct": round(oi_chg, 6) if oi_chg is not None else None,
        "price_change_pct": round(price_chg, 6) if price_chg is not None else None,
        "ts": observed_ms,
    }

    if update_index is not None:
        rows[update_index] = row
    else:
        if rows and prev_oi is not None and rows[-1].get("open_interest") is None:
            rows[-1]["open_interest"] = prev_oi
            rows[-1]["oi"] = prev_oi
        rows.append(row)

    p.write_text(json.dumps(rows[-500:]))
    return {"path": str(p), "bars": len(rows), "oi": oi, "oi_change_pct": oi_chg, "updated_in_place": update_index is not None}


def process(symbol, fg_val, fg_label, btc_chg=None):
    print(f"--- {symbol} ---")
    # Keep the existing Telegram card's current-bar behavior. Prediction
    # history and indicators below are derived only from completed bars.
    history, all_closes = fetch_binance(symbol, include_partial=True)
    closed_history = [bar for bar in history if bar.get("bar_complete") is True]
    closed_closes = [bar["price"] for bar in closed_history]
    oi, funding = fetch_okx(symbol)
    prev_oi = load_prev_oi(symbol)

    if history:
        history[-1]["oi"] = oi
        history[-1]["funding"] = funding
        history[-1]["open_interest"] = oi
        history[-1]["funding_rate"] = funding
        if prev_oi is not None and len(history) >= 2:
            history[-2]["oi"] = prev_oi
            history[-2]["open_interest"] = prev_oi

    price = closed_history[-1]["price"] if closed_history else None
    volume = closed_history[-1]["volume"] if closed_history else None
    prev_price = closed_history[-2]["price"] if len(closed_history) >= 2 else None
    price_chg = pct(price, prev_price)
    rsi_val = rsi(closed_closes, RSI_PERIOD)

    print(f"  OI now={oi} prev={prev_oi} chg={pct(oi, prev_oi) if oi is not None and prev_oi not in (None, 0) else None}")
    text = build_card(symbol, closed_history, closed_closes, oi, funding, fg_val, fg_label, btc_chg=btc_chg, prev_oi=prev_oi)
    ok = send_telegram(text)
    print(f"Telegram {symbol}:", "sent" if ok else "failed")
    save_hist(symbol, closed_history)
    save_last_oi(symbol, oi)
    latest = closed_history[-1] if closed_history else {}
    rich = update_rich_history(
        symbol, price, volume, oi, funding, rsi_val, prev_oi, price_chg,
        bar_open_ts=latest.get("bar_open_ts"),
        bar_close_ts=latest.get("bar_close_ts"),
        captured_at_ms=latest.get("captured_at_ms"),
    )
    print(f"  Rich history: bars={rich['bars']} oi={rich['oi']} oi_chg={rich['oi_change_pct']} in_place={rich['updated_in_place']}")


def main():
    print("Self-contained alert bot", datetime.now(timezone.utc).isoformat())
    fg_val, fg_label = fetch_fg()
    print(f"F&G: {fg_val} ({fg_label})")

    btc_chg = None
    try:
        _, btc_closes = fetch_binance(PRIMARY)
        if len(btc_closes) >= 2:
            btc_chg = (btc_closes[-1] - btc_closes[-2]) / btc_closes[-2] * 100
    except Exception as e:
        print("BTC relative:", e)

    for sym in SYMBOLS:
        try:
            process(sym, fg_val, fg_label, btc_chg=btc_chg)
            time.sleep(0.35)
        except Exception as e:
            print("Error", sym, e)
            send_telegram(f"{sym}: error — {e}")
    print("Done.")


if __name__ == "__main__":
    main()
