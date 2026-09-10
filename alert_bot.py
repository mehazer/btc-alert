"""
Multi-Coin Weekly RSI Alert Bot
--------------------------------
Binance'ten BTC/ETH/SOL (veya SYMBOLS ile belirtilen coinler) icin
haftalik mum verisini ceker, RSI + Volume hesaplar, Bybit'ten Open
Interest, alternative.me'den Fear & Greed Index, RSS'ten guncel haber
basliklarini ceker, esik asilirsa (veya ALWAYS_NOTIFY=true ise)
Telegram'a mesaj gonderir. Ayrica her calismada degerleri history/
klasorune JSON olarak kaydeder.

Bagimlilik: sadece 'requests'. Harici RSI/TA kutuphanesi kullanilmaz.

Guvenlik notu:
- Sadece resmi public API/RSS kaynaklarina istek atilir, hicbiri API
  key/secret gerektirmez.
- Telegram token/chat_id SADECE environment variable (GitHub Secrets)
  uzerinden okunur, koda asla yazilmaz.

Bilinen kisit:
- Open Interest icin once Binance Futures, sonra Bybit denendi;
  ikisi de GitHub Actions'in bulut IP'lerini engelliyor (451/403).
  OI su an "alinamadi" olarak gelir; ileride sabit IP'li ayri bir
  cozumle (kendi sunucu/VPS) tekrar ele alinacak. Script bu yuzden
  COKMEZ, sadece None/alinamadi yazar.
- Haber RSS kaynaklari (ozellikle CoinTelegraph) zaman zaman otomatik
  isteklere karsi korumaya (Cloudflare vb.) takilabilir; bu durumda
  o kaynak atlanir, digeri calismaya devam eder.
"""

import os
import sys
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

# ---- Ayarlar (environment variable / GitHub Secrets ile gelir) ----
SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT").split(",") if s.strip()]

INTERVAL = os.environ.get("INTERVAL", "1w")      # haftalik mum
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", "14"))
RSI_THRESHOLD = float(os.environ.get("RSI_THRESHOLD", "80"))

# Test modu: "true" yaparsan esik asilmasa bile her sembol icin mesaj gelir.
# Test bitince mutlaka "false"a geri al (ya da workflow'dan bu satiri sil).
ALWAYS_NOTIFY = os.environ.get("ALWAYS_NOTIFY", "false").lower() == "true"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BINANCE_SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
BYBIT_OI_URL = "https://api.bybit.com/v5/market/open-interest"
FEAR_GREED_URL = "https://api.alternative.me/fng/"

NEWS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
]
NEWS_HEADLINE_LIMIT = 4  # toplam kac baslik gosterilsin

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
    closes = [float(candle[4]) for candle in data]
    volumes = [float(candle[5]) for candle in data]
    return closes, volumes


def fetch_open_interest(symbol: str):
    """Bybit public API'den anlik Open Interest ceker (linear/USDT-margined).
    API key gerekmez. Su an GitHub Actions IP'lerinden 403 alinabiliyor
    (bilinen kisit, dosya basindaki nota bakin)."""
    params = {
        "category": "linear",
        "symbol": symbol,
        "intervalTime": "1h",
        "limit": 1,
    }
    resp = requests.get(BYBIT_OI_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    result_list = data.get("result", {}).get("list", [])
    if not result_list:
        raise ValueError(f"Bybit OI verisi bos dondu: {data}")
    return float(result_list[0]["openInterest"])


def fetch_fear_greed_index():
    """alternative.me'den guncel Fear & Greed Index'i ceker (0-100).
    Piyasa geneli icin tek bir deger, sembole ozel degildir.
    API key gerekmez."""
    resp = requests.get(FEAR_GREED_URL, params={"limit": 1}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    entry = data["data"][0]
    return int(entry["value"]), entry["value_classification"]


def fetch_news_headlines(limit=NEWS_HEADLINE_LIMIT):
    """RSS'ten guncel kripto haber basliklarini ceker. Sadece basliklar
    alinir, tam metin/icerik alinmaz. API key gerekmez."""
    headlines = []
    for source, url in NEWS_FEEDS:
        try:
            resp = requests.get(
                url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:limit]:
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append(f"[{source}] {title_el.text.strip()}")
        except Exception as e:
            print(f"{source} RSS cekilemedi (calismaya devam ediliyor): {e}")
    return headlines[:limit]


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

    # 2) Open Interest (Bybit) - kritik degil, basarisiz olursa
    #    calismaya devam edilir ama None olarak kaydedilir.
    open_interest = None
    try:
        open_interest = fetch_open_interest(symbol)
    except Exception as e:
        print(f"{symbol}: Open Interest cekilemedi (calismaya devam ediliyor): {e}")

    print(
        f"{symbol} | Weekly RSI({RSI_PERIOD}) = {rsi} | Fiyat = {last_price} "
        f"| Hacim = {last_volume} | Open Interest = {open_interest} "
        f"| Fear&Greed = {fear_greed_value} ({fear_greed_label})"
    )

    # 3) Tarihceye kaydet
    history = load_history(symbol)
    history.append({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "rsi": rsi,
        "price": last_price,
        "volume": last_volume,
        "open_interest": open_interest,
        "fear_greed_value": fear_greed_value,
        "fear_greed_label": fear_greed_label,
    })
    save_history(symbol, history)

    # 4) Esik kontrolu (ALWAYS_NOTIFY=true ise esik asilmasa da mesaj gider)
    threshold_exceeded = rsi >= RSI_THRESHOLD
    if threshold_exceeded or ALWAYS_NOTIFY:
        
        oi_text = open_interest if open_interest is not None else "alinamadi"
        news_text = "\n".join(news_headlines) if news_headlines else "alinamadi"
        reason = (
            "GHC hedge tetikleyici seviyesi asildi"
            if threshold_exceeded else "Test modu (ALWAYS_NOTIFY=true)"
        )
        message = (
            f"⚠️ {symbol} Haftalik RSI Uyarisi\n"
            f"RSI({RSI_PERIOD}): {rsi}\n"
            f"Esik: {RSI_THRESHOLD}\n"
            f"Son fiyat: {last_price}\n"
            f"Hacim (son hafta): {last_volume}\n"
            f"Open Interest: {oi_text}\n"
            f"({reason})"
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
    # Fear & Greed piyasa geneli icin tek deger, tum semboller icin
    # bir kere cekilir.
    fear_greed_value, fear_greed_label = None, None
    try:
        fear_greed_value, fear_greed_label = fetch_fear_greed_index()
        print(f"Fear & Greed Index = {fear_greed_value} ({fear_greed_label})")
    except Exception as e:
        print(f"Fear & Greed Index cekilemedi (calismaya devam ediliyor): {e}")

    # Haber basliklari da piyasa geneli icin tek seferlik cekiliyor.
    news_headlines = fetch_news_headlines()
    if news_headlines:
        headline_text = "\n".join(news_headlines)
        print("Guncel basliklar:")
        print(headline_text)

    # Piyasa geneli bilgisini (F&G + haberler) TEK bir mesaj olarak,
    # sembol basina tekrar etmeden gonder.
    fg_text = (
        f"{fear_greed_value} ({fear_greed_label})"
        if fear_greed_value is not None else "alinamadi"
    )
    news_text = "\n".join(news_headlines) if news_headlines else "alinamadi"
    market_message = (
        "📊 Genel Piyasa Durumu\n"
        f"Fear & Greed Index: {fg_text}\n"
        f"Haberler:\n{news_text}"
    )
    send_telegram_message(market_message)

    any_failure = False
    for symbol in SYMBOLS:
        ok = process_symbol(symbol)
        if not ok:
            any_failure = True

    if any_failure:
        sys.exit(1)

    
