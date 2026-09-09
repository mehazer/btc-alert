"""
BTC Weekly RSI Alert Bot
-------------------------
Binance'ten BTC/USDT haftalık mum verisini ceker, RSI hesaplar,
esik (varsayilan 80) asilirsa Telegram'a mesaj gonderir.

Bagimlilik: sadece 'requests'. Harici RSI/TA kutuphanesi kullanilmaz
(GitHub Actions gibi ortamlarda derleme/kurulum sorunu cikarmasin diye).
"""

import os
import sys
import requests

# ---- Ayarlar (environment variable / GitHub Secrets ile gelir) ----
SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")
INTERVAL = os.environ.get("INTERVAL", "1w")      # haftalik mum
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", "14"))
RSI_THRESHOLD = float(os.environ.get("RSI_THRESHOLD", "80"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BINANCE_KLINES_URL = "https://api.binance.vision/api/v3/klines"


def fetch_klines(symbol: str, interval: str, limit: int = 200):
    """Binance public API'den mum verisi ceker. API key gerekmez."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # Her eleman: [open_time, open, high, low, close, volume, ...]
    closes = [float(candle[4]) for candle in data]
    return closes


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


def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik, mesaj gonderilemedi.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    resp = requests.post(url, data=payload, timeout=15)

    if resp.status_code != 200:
        print(f"Telegram gonderim hatasi: {resp.status_code} {resp.text}")
        return False
    return True


def main():
    try:
        closes = fetch_klines(SYMBOL, INTERVAL, limit=RSI_PERIOD + 50)
    except Exception as e:
        print(f"Binance veri cekme hatasi: {e}")
        sys.exit(1)

    try:
        rsi = compute_rsi(closes, RSI_PERIOD)
    except Exception as e:
        print(f"RSI hesaplama hatasi: {e}")
        sys.exit(1)

    last_price = closes[-1]
    print(f"{SYMBOL} | Weekly RSI({RSI_PERIOD}) = {rsi} | Son fiyat = {last_price}")

    if rsi >= RSI_THRESHOLD:
        message = (
            f"⚠️ {SYMBOL} Haftalik RSI Uyarisi\n"
            f"RSI({RSI_PERIOD}): {rsi}\n"
            f"Esik: {RSI_THRESHOLD}\n"
            f"Son fiyat: {last_price}\n"
            f"(GHC hedge tetikleyici seviyesi asildi)"
        )
        sent = send_telegram_message(message)
        print("Telegram mesaji gonderildi." if sent else "Telegram mesaji gonderilemedi.")
    else:
        print("Esik asilmadi, bildirim gonderilmedi.")


if __name__ == "__main__":
    main()
