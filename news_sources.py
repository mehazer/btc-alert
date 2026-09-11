"""
news_sources.py
------------------------------
Haber kaynaklarini (RSS + Telegram kanallari) tek bir yerden
yonetir. news_bot.py bu moduldeki fonksiyonlari kullanir.
Kaynak listesi (NEWS_SOURCES) dashboard'un "nereden haber
cekiliyor" listesini beslemek icin de kullanilabilir.
"""

import requests
import xml.etree.ElementTree as ET

RSS_SOURCES = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss"},
]

TELEGRAM_SOURCES = [
    {"name": "CoinDesk (Telegram)", "username": "coindesk"},
    {"name": "CoinTelegraph (Telegram)", "username": "cointelegraph"},
    {"name": "Coin Bureau Insider (Telegram)", "username": "cbinsider"},
]

HEADLINE_LIMIT_PER_SOURCE = 4


def list_all_sources():
    """Dashboard icin: tum kaynaklarin adi + tipini duz liste olarak doner."""
    sources = [{"name": s["name"], "type": "rss"} for s in RSS_SOURCES]
    sources += [{"name": s["name"], "type": "telegram"} for s in TELEGRAM_SOURCES]
    return sources


def fetch_rss_headlines(limit=HEADLINE_LIMIT_PER_SOURCE):
    headlines = []
    for source in RSS_SOURCES:
        try:
            resp = requests.get(source["url"], timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:limit]:
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append({
                        "source": source["name"],
                        "type": "rss",
                        "headline": title_el.text.strip(),
                    })
        except Exception as e:
            print(f"{source['name']} RSS cekilemedi (calismaya devam ediliyor): {e}")
    return headlines


def fetch_telegram_headlines(limit=HEADLINE_LIMIT_PER_SOURCE):
    from bs4 import BeautifulSoup
    headlines = []
    for source in TELEGRAM_SOURCES:
        url = f"https://t.me/s/{source['username']}"
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            messages = soup.find_all("div", class_="tgme_widget_message_text")
            for msg in messages[-limit:]:
                text = msg.get_text(separator=" ", strip=True)
                if text:
                    headlines.append({
                        "source": source["name"],
                        "type": "telegram",
                        "headline": text,
                    })
        except Exception as e:
            print(f"Telegram kanali cekilemedi ({source['username']}): {e}")
    return headlines


def fetch_all_headlines():
    return fetch_rss_headlines() + fetch_telegram_headlines()
