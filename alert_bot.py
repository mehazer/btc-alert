"""
Self-contained multi-coin alert bot.
Real data (Binance + OKX + F&G) + rich Telegram cards with figures.
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


def fetch_binance(symbol, limit=80):
    r = requests.get(BINANCE_KLINES, params={"symbol": symbol, "interval": INTERVAL, "limit": limit}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    history, closes = [], []
    for row in rows:
        close = float(row[4])
        vol = float(row[5])
        closes.append(close)
        history.append({
            "price": close,
            "volume": vol,
            "oi": None,
            "funding": None,
            "ts": row[0],
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
            oi = float(r.json()["data"][0].get("oi") or 0)
    except Exception as e:
        print("OI", symbol, e)
    try:
        r = requests.get(OKX_FUNDING, params={"instId": inst}, timeout=15)
        if r.ok and r.json().get("data"):
            funding = float(r.json()["data"][0].get("fundingRate") or 0)
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
    prices = [h["price"] for h in history[-lookback:] if h.get("price") is not None]
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


def build_card(symbol, history, closes, oi, funding, fg_val, fg_label, btc_chg=None):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    price = history[-1]["price"]
    prev_price = history[-2]["price"] if len(history) >= 2 else price
    price_chg = pct(price, prev_price)

    prev_oi = history[-2].get("oi") if len(history) >= 2 else None
    oi_chg = pct(oi, prev_oi) if oi is not None and prev_oi else None

    vols = [h["volume"] for h in history[-20:] if h.get("volume")]
    avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1) if len(vols) > 1 else (vols[0] if vols else None)
    vol_ratio = (history[-1]["volume"] / avg_vol) if avg_vol and avg_vol > 0 else None

    rsi_val = rsi(closes, RSI_PERIOD)
    sup, res = support_resistance(history)

    primary, secondary, risk = classify(price_chg, oi_chg, vol_ratio, funding)

    bullish = primary in ("Fresh long buildup", "Spot-led buying", "Short covering")
    bearish = primary in ("Fresh short buildup", "Long liquidation", "Soft selling / drift")
    if bullish:
        stance = "Constructive but elevated risk" if risk == "High" else ("Constructive — stay selective" if risk == "Moderate" else "Constructive watch")
    elif bearish:
        stance = "Defensive" if risk in ("High", "Very High") else "Cautious / defensive bias"
    else:
        stance = "Wait — no clear edge"

    if funding is not None and funding > 0.0003:
        regime, pressure = "Risk-on / crowded", "Crowded long"
    elif funding is not None and funding < -0.0003:
        regime, pressure = "Risk-off / shorts", "Crowded short"
    elif funding is not None and funding > 0.00005:
        regime, pressure = "Mild risk-on", "Mild long bias"
    elif funding is not None and funding < -0.00005:
        regime, pressure = "Mild risk-off", "Mild short bias"
    else:
        regime, pressure = "Neutral", "Normal"

    def fnum(x, d=2):
        return f"{x:,.{d}f}" if isinstance(x, (int, float)) else "n/a"

    def fchg(label, chg):
        if chg is None:
            return f"{label}: n/a"
        direction = "Rising" if chg > 0.3 else ("Falling" if chg < -0.3 else "Flat")
        return f"{label}: {direction} — {chg:+.2f}%"

    vol_str = "Volume: n/a"
    if vol_ratio is not None:
        cls = "Climactic" if vol_ratio >= 2.0 else ("Increasing" if vol_ratio >= 1.35 else ("Decreasing" if vol_ratio <= 0.7 else "Normal"))
        vol_str = f"Volume: {cls} ({vol_ratio:.2f}x avg)"

    fund_str = "Funding: n/a"
    if funding is not None:
        fl = "Strongly Positive" if funding >= 0.0003 else ("Positive" if funding > 0.00005 else ("Strongly Negative" if funding <= -0.0003 else ("Negative" if funding < -0.00005 else "Neutral")))
        fund_str = f"Funding: {fl} ({funding * 100:.4f}%)"

    trend = "Bullish-leaning" if rsi_val and rsi_val >= 60 else ("Bearish-leaning" if rsi_val and rsi_val <= 40 else "Range / neutral")

    lines = [
        f"{symbol} · {INTERVAL} · {now} UTC",
        f"Read: {primary}",
    ]
    if secondary:
        lines.append(f"Also: {secondary}")
    lines += [
        f"Stance: {stance}",
        f"Drop-risk: {risk}",
        "",
        "Figures:",
        f"• Price: {fnum(price)}  |  RSI: {fnum(rsi_val, 1)}",
        f"• S/R: {fnum(sup)} / {fnum(res)}",
        f"• {fchg('Price', price_chg)}",
        f"• {fchg('OI', oi_chg)}",
        f"• {vol_str}",
        f"• {fund_str}",
        f"• Trend: {trend}",
    ]

    if symbol != PRIMARY and btc_chg is not None and price_chg is not None:
        rel = price_chg - btc_chg
        rel_l = "Outperforming" if rel > 1 else ("Underperforming" if rel < -1 else "Similar")
        lines.append(f"• vs BTC: {rel_l} (BTC {btc_chg:+.2f}%)")

    ctx = [f"Regime: {regime}", f"Pressure: {pressure}"]
    if symbol == PRIMARY and fg_val is not None:
        ctx.append(f"F&G: {fg_val} ({fg_label or 'n/a'})")
    lines.append("")
    lines.append("Market: " + " | ".join(ctx))

    lines.append("")
    lines.append("Why:")
    if primary == "Fresh long buildup":
        lines.append("• Price up with OI expansion → leveraged participation / new longs.")
    elif primary == "Short covering":
        lines.append("• Price up while OI contracts → classic short covering footprint.")
    elif primary == "Long liquidation":
        lines.append("• Price down with OI contraction on volume → long unwinding.")
    elif primary == "Fresh short buildup":
        lines.append("• Price down with OI expansion → fresh short positioning.")
    else:
        lines.append(f"• Classified from price/OI/volume relationship as {primary}.")

    if risk in ("High", "Moderate"):
        lines.append("")
        lines.append(f"Risk driver: {'Sharp OI + positive funding' if (oi_chg or 0) > 3 else 'Participation / funding imbalance'}")

    lines.append("")
    if risk == "High":
        lines.append("Watch: Crowded positioning — watch for flush or rejection.")
    elif primary == "Range / no clear mechanism":
        lines.append("Watch: Wait for clearer price + OI agreement before acting.")
    else:
        lines.append("Watch: Price holds key level; OI does not spike vertically.")

    lines.append("")
    lines.append("Note: Mechanism read, not a trade signal.")
    return "\n".join(lines)


def load_prev_oi(symbol):
    p = HISTORY_DIR / f"{symbol}_{INTERVAL}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        for h in reversed(data[-40:]):
            if h.get("oi") is not None:
                return h["oi"]
    except Exception:
        pass
    return None


def save_hist(symbol, history):
    p = HISTORY_DIR / f"{symbol}_{INTERVAL}.json"
    p.write_text(json.dumps(history[-200:]))


def process(symbol, fg_val, fg_label, btc_chg=None):
    print(f"--- {symbol} ---")
    history, closes = fetch_binance(symbol)
    oi, funding = fetch_okx(symbol)
    if history:
        history[-1]["oi"] = oi
        history[-1]["funding"] = funding
        prev = load_prev_oi(symbol)
        if prev is not None and len(history) >= 2 and history[-2].get("oi") is None:
            history[-2]["oi"] = prev

    text = build_card(symbol, history, closes, oi, funding, fg_val, fg_label, btc_chg=btc_chg)
    ok = send_telegram(text)
    print(f"Telegram {symbol}:", "sent" if ok else "failed")
    save_hist(symbol, history)


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
