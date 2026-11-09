"""
Compact multi-coin alert bot — real data + rich Telegram cards.
Fetches price/RSI/volume (Binance), OI/funding (OKX), Fear&Greed.
"""
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from agents import mechanism_interpreter

SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()]
PRIMARY_SYMBOL = "BTCUSDT"
INTERVAL = os.environ.get("INTERVAL", "4h")
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", "14"))
ALWAYS_NOTIFY = os.environ.get("ALWAYS_NOTIFY", "true").lower() == "true"
NOTIFY_ALL_SYMBOLS = os.environ.get("NOTIFY_ALL_SYMBOLS", "true").lower() == "true"

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
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
        print(f"Telegram status: {r.status_code}")
        return r.ok
    except Exception as e:
        print("Telegram error:", e)
        return False


def rsi_from_closes(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def fetch_binance(symbol, interval=INTERVAL, limit=80):
    r = requests.get(
        BINANCE_KLINES,
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=20,
    )
    r.raise_for_status()
    rows = r.json()
    history = []
    closes = []
    for row in rows:
        close = float(row[4])
        vol = float(row[5])
        closes.append(close)
        history.append({
            "price": close,
            "volume": vol,
            "open_interest": None,
            "funding_rate": None,
            "date": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat(),
        })
    return history, closes


def fetch_okx_oi_funding(symbol):
    inst = OKX_MAP.get(symbol)
    if not inst:
        return None, None
    oi, funding = None, None
    try:
        r = requests.get(OKX_OI, params={"instId": inst}, timeout=15)
        if r.ok:
            data = r.json().get("data") or []
            if data:
                oi = float(data[0].get("oi", 0) or 0)
    except Exception as e:
        print(f"OI error {symbol}:", e)
    try:
        r = requests.get(OKX_FUNDING, params={"instId": inst}, timeout=15)
        if r.ok:
            data = r.json().get("data") or []
            if data:
                funding = float(data[0].get("fundingRate", 0) or 0)
    except Exception as e:
        print(f"Funding error {symbol}:", e)
    return oi, funding


def fetch_fear_greed():
    try:
        r = requests.get(FEAR_GREED, params={"limit": 1}, timeout=15)
        if r.ok:
            d = (r.json().get("data") or [{}])[0]
            return int(d.get("value", 0)), d.get("value_classification", "Unknown")
    except Exception as e:
        print("F&G error:", e)
    return None, None


def load_history(symbol):
    p = HISTORY_DIR / f"{symbol}_{INTERVAL}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return []
    return []


def save_history(symbol, history):
    p = HISTORY_DIR / f"{symbol}_{INTERVAL}.json"
    history = history[-200:]
    p.write_text(json.dumps(history, indent=None))


def process_symbol(symbol, fear_greed_value, fear_greed_label, btc_history=None, btc_chg=None):
    print(f"--- {symbol} ---")
    try:
        history, closes = fetch_binance(symbol)
    except Exception as e:
        print(f"Binance fail {symbol}:", e)
        send_telegram(f"{symbol}: data fetch failed ({e})")
        return

    oi, funding = fetch_okx_oi_funding(symbol)
    if history:
        history[-1]["open_interest"] = oi
        history[-1]["funding_rate"] = funding

    prev_hist = load_history(symbol)
    if prev_hist and history:
        last_oi = None
        for h in prev_hist[-30:]:
            if h.get("open_interest") is not None:
                last_oi = h["open_interest"]
        if last_oi is not None and history[-2].get("open_interest") is None:
            history[-2]["open_interest"] = last_oi

    current_price = history[-1]["price"] if history else None
    current_vol = history[-1]["volume"] if history else None
    rsi = rsi_from_closes(closes, RSI_PERIOD)

    analysis = mechanism_interpreter.analyze(
        symbol=symbol,
        history=history,
        current_price=current_price,
        current_oi=oi,
        current_volume=current_vol,
        current_funding=funding,
        current_rsi=rsi,
        btc_history=btc_history,
        btc_price_change_pct=btc_chg,
        timeframe=INTERVAL,
    )

    regime = "Neutral"
    pressure = "Normal"
    if funding is not None:
        if funding > 0.0003:
            pressure = "Crowded long"
            regime = "Risk-on / crowded"
        elif funding < -0.0003:
            pressure = "Crowded short"
            regime = "Risk-off / shorts"
        elif funding > 0.00005:
            pressure = "Mild long bias"
        elif funding < -0.00005:
            pressure = "Mild short bias"

    extra = {
        "regime": regime,
        "pressure": pressure,
        "fear_greed_value": fear_greed_value if symbol == PRIMARY_SYMBOL else None,
        "fear_greed_label": fear_greed_label if symbol == PRIMARY_SYMBOL else None,
    }

    if ALWAYS_NOTIFY or symbol == PRIMARY_SYMBOL or NOTIFY_ALL_SYMBOLS:
        text = mechanism_interpreter.format_telegram(analysis, extra_context=extra)
        ok = send_telegram(text)
        print(f"Telegram {symbol}:", "sent" if ok else "failed")

    save_history(symbol, history)
    return analysis


def main():
    print("Starting compact alert bot", datetime.now(timezone.utc).isoformat())
    fg_val, fg_label = fetch_fear_greed()
    print(f"Fear & Greed: {fg_val} ({fg_label})")

    btc_hist = None
    btc_chg = None
    try:
        btc_hist, btc_closes = fetch_binance(PRIMARY_SYMBOL)
        if len(btc_closes) >= 2:
            btc_chg = (btc_closes[-1] - btc_closes[-2]) / btc_closes[-2] * 100
    except Exception as e:
        print("BTC fetch for relative:", e)

    for sym in SYMBOLS:
        try:
            process_symbol(sym, fg_val, fg_label, btc_history=btc_hist, btc_chg=btc_chg)
            time.sleep(0.4)
        except Exception as e:
            print(f"Error {sym}:", e)
            send_telegram(f"{sym}: processing error — {e}")

    print("Done.")


if __name__ == "__main__":
    main()
