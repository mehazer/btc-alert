"""
Multi-Coin Daily RSI Alert Bot + Market Agent (v2)
------------------------------------------------
Binance'ten gunluk mum + RSI, OKX'ten Open Interest + Funding Rate,
alternative.me'den Fear & Greed, mevcut haberlerden basit sentiment
skoru toplar. Market Agent bu sinyalleri yonlu (directional) bir
degerlendirmeye cevirir.

Bagimlilik: sadece 'requests' (+ mevcut news_sources icin bs4).
"""

import os
import sys
import json
from datetime import datetime, timezone

import requests

from agents import market_agent
from agents import news_sentiment

# ---- Ayarlar ----
SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT").split(",") if s.strip()]
PRIMARY_SYMBOL = "BTCUSDT"

INTERVAL = os.environ.get("INTERVAL", "1d")
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", "14"))
RSI_THRESHOLD = float(os.environ.get("RSI_THRESHOLD", "80"))

ALWAYS_NOTIFY = os.environ.get("ALWAYS_NOTIFY", "false").lower() == "true"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BINANCE_SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
OKX_OI_URL = "https://www.okx.com/api/v5/public/open-interest"
OKX_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate"
FEAR_GREED_URL = "https://api.alternative.me/fng/"

HISTORY_DIR = "history"
MAX_HISTORY_ENTRIES = 200
MAX_MARKET_AGENT_ENTRIES = 200

OKX_SYMBOL_MAP = {
    "BTCUSDT": "BTC-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
    "SOLUSDT": "SOL-USDT-SWAP",
}


def _okx_inst_id(symbol):
    inst_id = OKX_SYMBOL_MAP.get(symbol.upper())
    if not inst_id:
        base = symbol.upper().replace("USDT", "")
        inst_id = f"{base}-USDT-SWAP"
    return inst_id


# ---------------------------------------------------------------------
# Veri cekme
# ---------------------------------------------------------------------

def fetch_klines(symbol, interval, limit=200):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_SPOT_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    closes = [float(candle[4]) for candle in data]
    volumes = [float(candle[5]) for candle in data]
    return closes, volumes


def fetch_open_interest(symbol):
    inst_id = _okx_inst_id(symbol)
    params = {"instType": "SWAP", "instId": inst_id}
    resp = requests.get(OKX_OI_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise ValueError(f"OKX OI hata: {data}")
    result_list = data.get("data", [])
    if not result_list:
        raise ValueError(f"OKX OI bos: {data}")
    return float(result_list[0]["oiCcy"])


def fetch_funding_rate(symbol):
    """OKX current funding rate (e.g. 0.0001 = +0.01%)."""
    inst_id = _okx_inst_id(symbol)
    params = {"instId": inst_id}
    resp = requests.get(OKX_FUNDING_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "0":
        raise ValueError(f"OKX Funding hata: {data}")
    result_list = data.get("data", [])
    if not result_list:
        raise ValueError(f"OKX Funding bos: {data}")
    return float(result_list[0]["fundingRate"])


def fetch_fear_greed_index():
    resp = requests.get(FEAR_GREED_URL, params={"limit": 1}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    entry = data["data"][0]
    return int(entry["value"]), entry["value_classification"]


# ---------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------

def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        raise ValueError(f"RSI icin en az {period + 1} mum gerekli, {len(closes)} geldi.")
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ---------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------

def load_history(symbol):
    path = os.path.join(HISTORY_DIR, f"{symbol}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"{symbol}: history okunamadi ({e})")
        return []


def save_history(symbol, records):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"{symbol}.json")
    trimmed = records[-MAX_HISTORY_ENTRIES:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2, ensure_ascii=False)


def save_market_snapshot(fear_greed_value, fear_greed_label, funding_rate=None, news_sentiment=None):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, "market.json")
    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "fear_greed_value": fear_greed_value,
        "fear_greed_label": fear_greed_label,
        "funding_rate": funding_rate,
        "news_sentiment": news_sentiment,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)


def load_market_agent_history():
    path = os.path.join(HISTORY_DIR, "market_agent_history.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save_market_agent_history(records):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, "market_agent_history.json")
    trimmed = records[-MAX_MARKET_AGENT_ENTRIES:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets eksik, mesaj gonderilemedi.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        resp = requests.post(url, data=payload, timeout=15)
    except requests.RequestException as e:
        print(f"Telegram baglanti hatasi: {e}")
        return False
    if resp.status_code != 200:
        print(f"Telegram hata: {resp.status_code} {resp.text}")
        return False
    return True


# ---------------------------------------------------------------------
# Symbol processing
# ---------------------------------------------------------------------

def process_symbol(symbol, fear_greed_value, fear_greed_label):
    try:
        closes, volumes = fetch_klines(symbol, INTERVAL, limit=RSI_PERIOD + 50)
    except Exception as e:
        error_msg = f"⚠️ {symbol}: Binance veri hatasi\n{e}"
        print(error_msg)
        send_telegram_message(error_msg)
        return None

    try:
        rsi = compute_rsi(closes, RSI_PERIOD)
    except Exception as e:
        error_msg = f"⚠️ {symbol}: RSI hatasi\n{e}"
        print(error_msg)
        send_telegram_message(error_msg)
        return None

    last_price = closes[-1]
    last_volume = volumes[-1]

    open_interest = None
    try:
        open_interest = fetch_open_interest(symbol)
    except Exception as e:
        print(f"{symbol}: OI cekilemedi: {e}")

    funding_rate = None
    try:
        funding_rate = fetch_funding_rate(symbol)
    except Exception as e:
        print(f"{symbol}: Funding Rate cekilemedi: {e}")

    history = load_history(symbol)
    previous_entry = history[-1] if history else None

    price_change_pct = None
    if previous_entry and previous_entry.get("price"):
        prev_price = previous_entry["price"]
        price_change_pct = ((last_price - prev_price) / prev_price) * 100

    oi_change_pct = None
    if (
        previous_entry
        and previous_entry.get("open_interest") is not None
        and open_interest is not None
        and previous_entry["open_interest"] != 0
    ):
        prev_oi = previous_entry["open_interest"]
        oi_change_pct = ((open_interest - prev_oi) / prev_oi) * 100

    funding_pct_str = f"{funding_rate * 100:.4f}%" if funding_rate is not None else "n/a"

    print(
        f"{symbol} | RSI={rsi} | Price={last_price} | OI={open_interest} "
        f"| Funding={funding_pct_str} | dPrice={price_change_pct} | dOI={oi_change_pct}"
    )

    history.append({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "rsi": rsi,
        "price": last_price,
        "volume": last_volume,
        "open_interest": open_interest,
        "funding_rate": funding_rate,
        "fear_greed_value": fear_greed_value,
        "fear_greed_label": fear_greed_label,
    })
    save_history(symbol, history)

    # Optional per-symbol RSI alert (kept for backward compatibility)
    threshold_exceeded = rsi >= RSI_THRESHOLD
    if threshold_exceeded or ALWAYS_NOTIFY:
        fg_text = f"{fear_greed_value} ({fear_greed_label})" if fear_greed_value is not None else "n/a"
        oi_text = f"{open_interest:.2f}" if open_interest is not None else "n/a"
        reason = "RSI esigi asildi" if threshold_exceeded else "Test modu (ALWAYS_NOTIFY)"
        message = (
            f"⚠️ {symbol} Gunluk RSI\n"
            f"RSI({RSI_PERIOD}): {rsi}\n"
            f"Fiyat: {last_price}\n"
            f"OI: {oi_text}\n"
            f"Funding: {funding_pct_str}\n"
            f"Fear&Greed: {fg_text}\n"
            f"({reason})"
        )
        sent = send_telegram_message(message)
        print(f"{symbol}: Telegram " + ("gonderildi" if sent else "gonderilemedi"))

    return {
        "rsi": rsi,
        "price": last_price,
        "open_interest": open_interest,
        "funding_rate": funding_rate,
        "price_change_pct": price_change_pct,
        "oi_change_pct": oi_change_pct,
    }


# ---------------------------------------------------------------------
# Market Agent runner
# ---------------------------------------------------------------------

def run_market_agent(primary_symbol_data, fear_greed_value, news_sent):
    if primary_symbol_data is None:
        print("Market Agent atlandi: BTCUSDT verisi yok.")
        return None

    result = market_agent.evaluate(
        rsi=primary_symbol_data.get("rsi"),
        fear_greed_value=fear_greed_value,
        funding_rate=primary_symbol_data.get("funding_rate"),
        oi_change_pct=primary_symbol_data.get("oi_change_pct"),
        price_change_pct=primary_symbol_data.get("price_change_pct"),
        news_sentiment=news_sent.get("score") if news_sent else None,
    )

    print(
        f"Market Agent | State={result['state']} | Bias={result['bias_score']} "
        f"| Intensity={result['intensity']} | Confidence={result['confidence']}%"
    )
    for r in result["reasons"]:
        print(f"  - {r}")

    # Rich Telegram message
    funding = primary_symbol_data.get("funding_rate")
    funding_str = f"{funding * 100:.4f}%" if funding is not None else "n/a"
    news_str = f"{news_sent['score']:+.0f} ({news_sent['label']})" if news_sent else "n/a"

    reasons_text = "\n".join(f"• {r}" for r in result["reasons"])
    telegram_text = (
        f"🤖 Market Agent v2\n"
        f"Durum: *{result['state']}*\n"
        f"Bias: {result['bias_score']:+d} | Intensity: {result['intensity']}\n"
        f"Guven: {result['confidence']}%\n\n"
        f"RSI: {primary_symbol_data.get('rsi')}\n"
        f"Funding: {funding_str}\n"
        f"News Sentiment: {news_str}\n\n"
        f"Gerekceler:\n{reasons_text}"
    )
    # Telegram Markdown can be fragile; send as plain text
    telegram_text = telegram_text.replace("*", "")
    sent = send_telegram_message(telegram_text)
    print("Market Agent Telegram: " + ("gonderildi" if sent else "gonderilemedi"))

    agent_history = load_market_agent_history()
    agent_history.append({
        "timestamp": result["evaluated_at"],
        "state": result["state"],
        "bias_score": result["bias_score"],
        "intensity": result["intensity"],
        "confidence": result["confidence"],
        "reasons": result["reasons"],
        "btc_price": primary_symbol_data.get("price"),
        "rsi": primary_symbol_data.get("rsi"),
        "funding_rate": primary_symbol_data.get("funding_rate"),
        "oi_change_pct": primary_symbol_data.get("oi_change_pct"),
        "price_change_pct": primary_symbol_data.get("price_change_pct"),
        "fear_greed_value": fear_greed_value,
        "news_sentiment": news_sent.get("score") if news_sent else None,
    })
    save_market_agent_history(agent_history)

    return result


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

def main():
    # 1. Fear & Greed
    fear_greed_value, fear_greed_label = None, None
    try:
        fear_greed_value, fear_greed_label = fetch_fear_greed_index()
        print(f"Fear & Greed = {fear_greed_value} ({fear_greed_label})")
    except Exception as e:
        print(f"Fear & Greed cekilemedi: {e}")

    # 2. News sentiment (from already collected headlines)
    news_sent = news_sentiment.load_and_score(HISTORY_DIR)
    print(
        f"News Sentiment = {news_sent['score']:+.1f} ({news_sent['label']}) "
        f"| headlines={news_sent['total_headlines']} "
        f"| bull_hits={news_sent['bullish_hits']} bear_hits={news_sent['bearish_hits']}"
    )

    # 3. Process symbols
    any_failure = False
    primary_symbol_data = None

    for symbol in SYMBOLS:
        symbol_data = process_symbol(symbol, fear_greed_value, fear_greed_label)
        if symbol_data is None:
            any_failure = True
        elif symbol.upper() == PRIMARY_SYMBOL:
            primary_symbol_data = symbol_data

    # 4. Save market snapshot (includes funding + news)
    funding_for_snapshot = primary_symbol_data.get("funding_rate") if primary_symbol_data else None
    save_market_snapshot(
        fear_greed_value,
        fear_greed_label,
        funding_rate=funding_for_snapshot,
        news_sentiment=news_sent.get("score"),
    )

    # 5. Run directional Market Agent
    run_market_agent(primary_symbol_data, fear_greed_value, news_sent)

    if any_failure:
        sys.exit(1)


if __name__ == "__main__":
    main()
