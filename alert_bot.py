"""
Multi-Coin Weekly RSI Alert Bot
--------------------------------
Binance'ten BTC/ETH/SOL (veya SYMBOLS ile belirtilen coinler) icin
haftalik mum verisini ceker, RSI + Volume hesaplar, Binance Futures'tan
Open Interest ceker, esik asilirsa Telegram'a mesaj gonderir.
Ayrica her calismada RSI/fiyat/volume/OI degerlerini history/ klasorune
JSON olarak kaydeder (tarihce).

Bagimlilik: sadece 'requests'. Harici RSI/TA kutuphanesi kullanilmaz.

Guvenlik notu:
- Sadece Binance'in resmi public API'lerine (spot + futures) istek atilir.
- API key/secret KULLANILMAZ (hicbiri gerekmiyor, sadece public data okunur).
- Telegram token/chat_id SADECE environment variable (GitHub Secrets)
  uzerinden okunur, koda asla yazilmaz.
"""

import os
import sys
import json
from datetime import datetime, timezone

import requests

# ---- Ayarlar (environment variable / GitHub Secrets ile gelir) ----
# Birden fazla sembol icin virgul ile ayirin: "BTCUSDT,ETHUSDT,SOLUSDT"
SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT").split(",") if s.strip()]

INTERVAL = os.environ.get("INTERVAL", "1w")      # haftalik mum
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", "14"))
RSI_THRESHOLD = float(os.environ.get("RSI_THRESHOLD", "80"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BINANCE_SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_FUTURES_OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"

HISTORY_DIR = "history"
MAX_HISTORY_ENTRIES = 200  # ~200 hafta (~4 yil) - dosyalarin sisirmemesi icin


# ---------------------------------------------------------------------
# Veri cekme
# ---------------------------------------------------------------------

def fetch_klines(symbol: str, interval: str, limit: int = 200):
    """Binance public SPOT API'den mum verisi ceker. API key gerekmez.
    Kapanis fiyatlarini ve hacimleri (volume) dondurur."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_SPOT_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # Her eleman: [open_time, open, high, low, close, volume, ...]
    closes = [float(candle[4]) for candle in data]
    volumes = [float(candle[5]) for candle in data]
    return closes, volumes


def fetch_open_interest(symbol: str):
    """Binance public FUTURES API'den anlik Open Interest ceker.
    Not: Open Interest sadece Futures (vadeli islem) piyasasinda vardir,
    Spot piyasada bu kavram yoktur. API key gerekmez."""
    resp = requests.get(BINANCE_FUTURES_OI_URL, params={"symbol": symbol}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return float(data["openInterest"])


# ---------------------------------------------------------------------
# RSI hesaplama
# ---------------------------------------------------------------------

def compute_rsi(closes, period: int = 14):
    """Klasik Wilder RSI hesaplamasi (ek kutuphane gerektirmez)."""
    if len(closes) < period + 1:
        raise ValueError(
            f"RSI hesaplamak icin en az {period + 1} mum gerekli, "
            f"{len(closes)} mum geldi."
        )

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
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


# ---------------------------------------------------------------------
# Tarihce (history) okuma / yazma
# ---------------------------------------------------------------------

def load_history(symbol: str):
    path = os.path.join(HISTORY_DIR, f"{symbol}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"{symbol}: history dosyasi okunamadi, sifirdan baslaniyor ({e})")
        return []


def save_history(symbol: str, records):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"{symbol}.json")
    trimmed = records[-MAX_HISTORY_ENTRIES:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------

def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik, mesaj gonderilemedi.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        resp = requests.post(url, data=payload, timeout=15)
    except requests.RequestException as e:
        print(f"Telegram'a baglanilamadi: {e}")
        return False

    if resp.status_code != 200:
        print(f"Telegram gonderim hatasi: {resp.status_code} {resp.text}")
        return False
    return True


# ---------------------------------------------------------------------
# Bir sembolu isle
# ---------------------------------------------------------------------

def process_symbol(symbol: str):
    # 1) Spot kline verisi (fiyat + hacim + RSI icin)
    try:
        closes, volumes = fetch_klines(symbol, INTERVAL, limit=RSI_PERIOD + 50)
    except Exception as e:
        error_msg = f"⚠️ {symbol}: Binance (spot) veri cekme hatasi\n{e}"
        print(error_msg)
        send_telegram_message(error_msg)
        return False

    try:
        rsi = compute_rsi(closes, RSI_PERIOD)
    except Exception as e:
        error_msg = f"⚠️ {symbol}: RSI hesaplama hatasi\n{e}"
        print(error_msg)
        send_telegram_message(error_msg)
        return False

    last_price = closes[-1]
    last_volume = volumes[-1]

    # 2) Open Interest (Futures API) - kritik degil, basarisiz olursa
    #    calismaya devam edilir ama None olarak kaydedilir.
    open_interest = None
    try:
        open_interest = fetch_open_interest(symbol)
    except Exception as e:
        print(f"{symbol}: Open Interest cekilemedi (calismaya devam ediliyor): {e}")

    print(
        f"{symbol} | Weekly RSI({RSI_PERIOD}) = {rsi} | Fiyat = {last_price} "
        f"| Hacim = {last_volume} | Open Interest = {open_interest}"
    )

    # 3) Tarihceye kaydet
    history = load_history(symbol)
    history.append({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "rsi": rsi,
        "price": last_price,
        "volume": last_volume,
        "open_interest": open_interest,
    })
    save_history(symbol, history)

    # 4) Esik kontrolu
    if rsi >= RSI_THRESHOLD:
        message = (
            f"⚠️ {symbol} Haftalik RSI Uyarisi\n"
            f"RSI({RSI_PERIOD}): {rsi}\n"
            f"Esik: {RSI_THRESHOLD}\n"
            f"Son fiyat: {last_price}\n"
            f"Hacim (son hafta): {last_volume}\n"
            f"Open Interest: {open_interest if open_interest is not None else 'alinamadi'}\n"
            f"(GHC hedge tetikleyici seviyesi asildi)"
        )
        sent = send_telegram_message(message)
        print(f"{symbol}: Telegram mesaji " + ("gonderildi." if sent else "gonderilemedi."))
    else:
        print(f"{symbol}: Esik asilmadi, bildirim gonderilmedi.")

    return True


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

def main():
    any_failure = False
    for symbol in SYMBOLS:
        ok = process_symbol(symbol)
        if not ok:
            any_failure = True

    if any_failure:
        # Workflow run'u "failed" olarak isaretlenir (Actions sekmesinde
        # kirmizi gorunur), boylece sessiz basarisizlik olmaz.
        sys.exit(1)


if __name__ == "__main__":
    main()
