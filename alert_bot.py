"""
Multi-Coin Weekly RSI Alert Bot
--------------------------------
Binance'ten BTC/ETH/SOL (veya SYMBOLS ile belirtilen coinler) icin
haftalik mum verisini ceker, RSI + Volume hesaplar, Bybit'ten Open
Interest, alternative.me'den Fear & Greed Index, RSS'ten haber
basliklarini, Reddit'ten (public JSON, key gerekmez) gundem
basliklarini ceker, esik asilirsa (veya ALWAYS_NOTIFY=true ise)
Telegram'a mesaj gonderir. Ayrica her calismada degerleri history/
klasorune JSON olarak kaydeder (sembol bazli + genel piyasa ozeti).

Bagimlilik: sadece 'requests'. Harici RSI/TA kutuphanesi kullanilmaz.

Bilinen kisitlar:
- Open Interest icin once Binance Futures, sonra Bybit denendi;
  ikisi de GitHub Actions'in bulut IP'lerini engelliyor (451/403).
  OI su an "alinamadi" olarak gelir; ileride sabit IP'li ayri bir
  cozumle (kendi sunucu/VPS) tekrar ele alinacak.
- Haber RSS kaynaklari (ozellikle CoinTelegraph) zaman zaman otomatik
  isteklere karsi korumaya (Cloudflare vb.) takilabilir.
- Reddit, User-Agent basligi olmayan/jenerik istekleri reddediyor;
  bu yuzden ozel bir User-Agent gonderiliyor. Yine de Reddit zaman
  zaman bulut IP'lerini rate-limit (429) ile gecici engelleyebilir;
  bu durumda script cokmez, sadece o kaynagi atlar.
"""

import os
import sys
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDT").split(",") if s.strip()]

INTERVAL = os.environ.get("INTERVAL", "1w")
RSI_PERIOD = int(os.environ.get("RSI_PERIOD", "14"))
RSI_THRESHOLD = float(os.environ.get("RSI_THRESHOLD", "80"))

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
NEWS_HEADLINE_LIMIT = 4

REDDIT_SUBREDDITS = ["CryptoCurrency"]
REDDIT_HEADLINE_LIMIT = 4
REDDIT_USER_AGENT = "btc-alert-bot/1.0 (personal weekly RSI alert tool)"

HISTORY_DIR = "history"
MAX_HISTORY_ENTRIES = 200


def fetch_klines(symbol, interval, limit=200):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(BINANCE_SPOT_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    closes = [float(candle[4]) for candle in data]
    volumes = [float(candle[5]) for candle in data]
    return closes, volumes


def fetch_open_interest(symbol):
    params = {"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 1}
    resp = requests.get(BYBIT_OI_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    result_list = data.get("result", {}).get("list", [])
    if not result_list:
        raise ValueError(f"Bybit OI verisi bos dondu: {data}")
    return float(result_list[0]["openInterest"])


def fetch_fear_greed_index():
    resp = requests.get(FEAR_GREED_URL, params={"limit": 1}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    entry = data["data"][0]
    return int(entry["value"]), entry["value_classification"]


def fetch_news_headlines(limit=NEWS_HEADLINE_LIMIT):
    headlines = []
    for source, url in NEWS_FEEDS:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:limit]:
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append(f"[{source}] {title_el.text.strip()}")
        except Exception as e:
            print(f"{source} RSS cekilemedi (calismaya devam ediliyor): {e}")
    return headlines[:limit]


def fetch_reddit_headlines(limit=REDDIT_HEADLINE_LIMIT):
    """Reddit'in public JSON uc noktasindan (login gerekmez) her
    subreddit icin en populer gonderi basliklarini ceker. Reddit
    ozel bir User-Agent olmadan istekleri reddediyor, o yuzden
    burada tanimlaniyor."""
    headlines = []
    headers = {"User-Agent": REDDIT_USER_AGENT}
    for sub in REDDIT_SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub}/hot.json"
        try:
            resp = requests.get(url, headers=headers, params={"limit": limit}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            children = data.get("data", {}).get("children", [])
            for child in children[:limit]:
                post = child.get("data", {})
                title = post.get("title")
                score = post.get("score", 0)
                if title:
                    headlines.append(f"[r/{sub}] {title} (👍{score})")
        except Exception as e:
            print(f"r/{sub} Reddit verisi cekilemedi (calismaya devam ediliyor): {e}")
    return headlines[:limit]


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        raise ValueError(f"RSI hesaplamak icin en az {period + 1} mum gerekli, {len(closes)} mum geldi.")
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


def load_history(symbol):
    path = os.path.join(HISTORY_DIR, f"{symbol}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"{symbol}: history dosyasi okunamadi, sifirdan baslaniyor ({e})")
        return []


def save_history(symbol, records):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"{symbol}.json")
    trimmed = records[-MAX_HISTORY_ENTRIES:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2, ensure_ascii=False)


def save_market_snapshot(fear_greed_value, fear_greed_label, news_headlines, reddit_headlines):
    """Piyasa geneli (sembole ozel olmayan) son durumu ayri bir
    dosyaya kaydeder, boylece dashboard sayfasi bunu okuyabilir."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, "market.json")
    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "fear_greed_value": fear_greed_value,
        "fear_greed_label": fear_greed_label,
        "news_headlines": news_headlines,
        "reddit_headlines": reddit_headlines,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)


def send_telegram_message(text):
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


def process_symbol(symbol, fear_greed_value, fear_greed_label, news_headlines, reddit_headlines):
    try:
        closes, volumes = fetch_klines(symbol, INTERVAL, limit=RSI_PERIOD + 50)
    except Exception as e:
        error_msg = f"⚠ {symbol}: Binance (spot) veri cekme hatasi\n{e}"
        print(error_msg)
        send_telegram_message(error_msg)
        return False

    try:
        rsi = compute_rsi(closes, RSI_PERIOD)
    except Exception as e:
        error_msg = f"⚠ {symbol}: RSI hesaplama hatasi\n{e}"
        print(error_msg)
        send_telegram_message(error_msg)
        return False

    last_price = closes[-1]
    last_volume = volumes[-1]

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

    threshold_exceeded = rsi >= RSI_THRESHOLD
    if threshold_exceeded or ALWAYS_NOTIFY:
        fg_text = f"{fear_greed_value} ({fear_greed_label})" if fear_greed_value is not None else "alinamadi"
        oi_text = open_interest if open_interest is not None else "alinamadi"
        news_text = "\n".join(news_headlines) if news_headlines else "alinamadi"
        reddit_text = "\n".join(reddit_headlines) if reddit_headlines else "alinamadi"
        reason = "GHC hedge tetikleyici seviyesi asildi" if threshold_exceeded else "Test modu (ALWAYS_NOTIFY=true)"
        message = (
            f"⚠ {symbol} Haftalik RSI Uyarisi\n"
            f"RSI({RSI_PERIOD}): {rsi}\n"
            f"Esik: {RSI_THRESHOLD}\n"
            f"Son fiyat: {last_price}\n"
            f"Hacim (son hafta): {last_volume}\n"
            f"Open Interest: {oi_text}\n"
            f"Fear & Greed Index: {fg_text}\n"
            f"Haberler:\n{news_text}\n"
            f"Reddit gundem:\n{reddit_text}\n"
            f"({reason})"
        )
        sent = send_telegram_message(message)
        print(f"{symbol}: Telegram mesaji " + ("gonderildi." if sent else "gonderilemedi."))
    else:
        print(f"{symbol}: Esik asilmadi, bildirim gonderilmedi.")

    return True


def main():
    fear_greed_value, fear_greed_label = None, None
    try:
        fear_greed_value, fear_greed_label = fetch_fear_greed_index()
        print(f"Fear & Greed Index = {fear_greed_value} ({fear_greed_label})")
    except Exception as e:
        print(f"Fear & Greed Index cekilemedi (calismaya devam ediliyor): {e}")

    news_headlines = fetch_news_headlines()
    if news_headlines:
        headline_text = "\n".join(news_headlines)
        print("Guncel basliklar:")
        print(headline_text)

    reddit_headlines = fetch_reddit_headlines()
    if reddit_headlines:
        reddit_text = "\n".join(reddit_headlines)
        print("Reddit gundemi:")
        print(reddit_text)

    save_market_snapshot(fear_greed_value, fear_greed_label, news_headlines, reddit_headlines)

    any_failure = False
    for symbol in SYMBOLS:
        ok = process_symbol(symbol, fear_greed_value, fear_greed_label, news_headlines, reddit_headlines)
        if not ok:
            any_failure = True

    if any_failure:
        sys.exit(1)


if __name__ == "__main__":
    main()
