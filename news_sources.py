"""
news_sources.py
------------------------------
Haber kaynaklarini (RSS + Google News + Telegram + Reddit + StockTwits + Yahoo)
tek bir yerden yonetir. news_bot.py bu moduldeki fonksiyonlari kullanir.
Kaynak listesi (list_all_sources) dashboard'un "nereden haber
fetched" list is also built from here.

Bilinen kisitlar:
- Reddit'in JSON API'si (r/<subreddit>/hot.json) kimlik dogrulama
  gerektirmez, ama GitHub Actions paylasimli IP'lerini zaman zaman
  may block it (429/403). On error the source is skipped silently.
- Google News RSS ucretsizdir, API key istemez. Sonuclar Google'in
  item comes from the news index; it is not a verbatim copy of web search results.
- StockTwits public stream is live-only (no historical archive).
- Yahoo Finance RSS is free and live-only; filtered to the last 24h.
"""

import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote_plus

from date_window import filter_items_to_window, recent_window_hours

RSS_SOURCES = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml"},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/feed"},
]

# Google News RSS — free, no API key.
# when:1h / when:4h / when:1d filter by recency (Google supports these).
GOOGLE_NEWS_QUERIES = [
    {
        "name": "Google News (BTC)",
        "query": "bitcoin OR BTC",
        "when": "4h",
    },
    {
        "name": "Google News (ETH)",
        "query": "ethereum OR ETH",
        "when": "4h",
    },
    {
        "name": "Google News (SOL)",
        "query": "solana OR SOL crypto",
        "when": "4h",
    },
    {
        "name": "Google News (ZEC)",
        "query": "zcash OR ZEC OR \"NU7\"",
        "when": "1d",
    },
    {
        "name": "Google News (ZAMA)",
        "query": "Zama OR \"fully homomorphic\" OR FHE crypto",
        "when": "1d",
    },
    {
        "name": "Google News (Macro)",
        "query": "crypto market OR bitcoin ETF OR fed rates OR SEC crypto",
        "when": "4h",
    },
]

TELEGRAM_SOURCES = [
    {"name": "CoinDesk (Telegram)", "username": "coindesk"},
    {"name": "CoinTelegraph (Telegram)", "username": "cointelegraph"},
    {"name": "Coin Bureau Insider (Telegram)", "username": "cbinsider"},
    {"name": "Watcher.Guru (Telegram)", "username": "watcherguru"},
    {"name": "Wu Blockchain (Telegram)", "username": "wublockchainenglish"},
    {"name": "Whale Alert (Telegram)", "username": "whale_alert_io"},
]

REDDIT_SOURCES = [
    {"name": "r/CryptoCurrency", "subreddit": "CryptoCurrency"},
    {"name": "r/Bitcoin", "subreddit": "Bitcoin"},
    {"name": "r/ethereum", "subreddit": "ethereum"},
]

STOCKTWITS_SYMBOLS = ["BTC", "ETH", "SOL"]
YAHOO_SYMBOLS = ["BTC", "ETH", "SOL"]

# Source quality is used for ranking and disclosure, not as proof that a
# headline is true. Google News is a discovery layer; it may surface low-value
# SEO, sponsored, or duplicated material and must not outrank direct reporting
# merely because it is recent.
SOURCE_QUALITY = {
    "primary": {"official", "sec", "cftc", "fed", "binance", "coinbase", "kraken"},
    "reputable": {
        "coindesk",
        "the block",
        "reuters",
        "bloomberg",
        "financial times",
        "ft",
        "wsj",
        "decrypt",
        "bitcoin magazine",
        "yahoo finance",
    },
    "discovery": {"google news"},
    "social": {"telegram", "reddit", "twitter", "stocktwits"},
}

HEADLINE_LIMIT_PER_SOURCE = 4
GOOGLE_NEWS_LIMIT = 5


def list_all_sources():
    """For the dashboard: returns every source name and type as a flat list."""
    sources = [{"name": s["name"], "type": "rss", "quality": source_quality(s["name"], "rss")} for s in RSS_SOURCES]
    sources += [{"name": s["name"], "type": "google_news", "quality": source_quality(s["name"], "google_news")} for s in GOOGLE_NEWS_QUERIES]
    sources += [{"name": s["name"], "type": "telegram", "quality": source_quality(s["name"], "telegram")} for s in TELEGRAM_SOURCES]
    sources += [{"name": s["name"], "type": "reddit", "quality": source_quality(s["name"], "reddit")} for s in REDDIT_SOURCES]
    sources += [
        {
            "name": f"StockTwits ({sym})",
            "type": "stocktwits",
            "quality": source_quality("stocktwits", "stocktwits"),
        }
        for sym in STOCKTWITS_SYMBOLS
    ]
    sources += [
        {
            "name": f"Yahoo Finance ({sym})",
            "type": "yahoo",
            "quality": source_quality("yahoo finance", "yahoo"),
        }
        for sym in YAHOO_SYMBOLS
    ]
    return sources


def source_quality(source_name, source_type=None):
    """Return a transparent quality tier for ranking and dashboard disclosure."""
    name = (source_name or "").lower()
    for tier, names in SOURCE_QUALITY.items():
        if any(token in name for token in names):
            return tier
    if source_type in {"telegram", "reddit", "stocktwits"}:
        return "social"
    if source_type == "google_news":
        return "discovery"
    if source_type == "yahoo":
        return "reputable"
    return "unknown"


def _clean_google_title(title):
    """Google News titles often end with ' - Publisher Name'."""
    if not title:
        return ""
    t = unescape(title).strip()
    # Strip trailing " - Source"
    t = re.sub(r"\s+[-–|]\s+[^\-–|]{2,40}$", "", t).strip()
    return t


def _google_news_rss_url(query, when=None):
    q = query
    if when:
        q = f"{query} when:{when}"
    # hl/gl/ceid keep results English-focused
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
    )


def fetch_google_news_headlines(limit=GOOGLE_NEWS_LIMIT):
    headlines = []
    for source in GOOGLE_NEWS_QUERIES:
        url = _google_news_rss_url(source["query"], source.get("when"))
        try:
            resp = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (compatible; btc-alert/1.0)"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:limit]:
                title_el = item.find("title")
                pub_el = item.find("pubDate")
                if title_el is None or not title_el.text:
                    continue
                headline = _clean_google_title(title_el.text)
                if not headline or len(headline) < 12:
                    continue
                entry = {
                    "source": source["name"],
                    "type": "google_news",
                    "headline": headline,
                }
                if pub_el is not None and pub_el.text:
                    entry["published"] = pub_el.text.strip()
                headlines.append(entry)
        except Exception as e:
            print(f"{source['name']} Google News fetch failed (continuing): {e}")
    return headlines


def fetch_rss_headlines(limit=HEADLINE_LIMIT_PER_SOURCE):
    headlines = []
    for source in RSS_SOURCES:
        try:
            resp = requests.get(
                source["url"],
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:limit]:
                title_el = item.find("title")
                pub_el = item.find("pubDate")
                if title_el is not None and title_el.text:
                    entry = {
                        "source": source["name"],
                        "type": "rss",
                        "headline": title_el.text.strip(),
                    }
                    if pub_el is not None and pub_el.text:
                        entry["published"] = pub_el.text.strip()
                    headlines.append(entry)
        except Exception as e:
            print(f"{source['name']} RSS fetch failed (continuing): {e}")
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
                        # Telegram public pages often lack reliable per-message
                        # timestamps in HTML; treat as live-only current scan.
                        "published": datetime.now(timezone.utc).isoformat(),
                    })
        except Exception as e:
            print(f"Telegram channel fetch failed ({source['username']}): {e}")
    return headlines


def fetch_reddit_headlines(limit=HEADLINE_LIMIT_PER_SOURCE):
    headlines = []
    for source in REDDIT_SOURCES:
        url = f"https://www.reddit.com/r/{source['subreddit']}/hot.json"
        try:
            resp = requests.get(
                url,
                params={"limit": limit},
                timeout=15,
                headers={"User-Agent": "btc-alert-news-bot/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            posts = data.get("data", {}).get("children", [])
            for post in posts[:limit]:
                title = post.get("data", {}).get("title")
                created = post.get("data", {}).get("created_utc")
                if title:
                    entry = {
                        "source": source["name"],
                        "type": "reddit",
                        "headline": title.strip(),
                    }
                    if created is not None:
                        entry["published"] = created
                    headlines.append(entry)
        except Exception as e:
            print(f"Reddit channel fetch failed ({source['subreddit']}): {e}")
    return headlines


def fetch_stocktwits_headlines(limit=HEADLINE_LIMIT_PER_SOURCE):
    """Live-only StockTwits messages exposed as social headlines."""
    try:
        import stocktwits_sentiment
    except Exception as e:
        print(f"StockTwits module unavailable: {e}")
        return []
    headlines = []
    for sym in STOCKTWITS_SYMBOLS:
        try:
            result = stocktwits_sentiment.fetch_symbol_stream(sym, limit=limit)
            for message in result.get("messages") or []:
                headlines.append({
                    "source": f"StockTwits ({sym})",
                    "type": "stocktwits",
                    "headline": message.get("headline") or "",
                    "published": message.get("published"),
                    "sentiment_label": message.get("sentiment_label"),
                    "symbol": sym,
                })
            if result.get("coverage_gap"):
                print(result["coverage_gap"])
        except Exception as e:
            print(f"StockTwits fetch failed ({sym}): {e}")
    return headlines


def fetch_yahoo_headlines(limit=HEADLINE_LIMIT_PER_SOURCE):
    """Free Yahoo Finance RSS for BTC/ETH/SOL — window-filtered."""
    try:
        import yahoo_news
    except Exception as e:
        print(f"Yahoo news module unavailable: {e}")
        return []
    headlines = []
    try:
        result = yahoo_news.fetch_multi(YAHOO_SYMBOLS, limit=limit)
        for item in result.get("headlines") or []:
            headlines.append(item)
        for sym, row in (result.get("symbols") or {}).items():
            if row.get("coverage_gap"):
                print(row["coverage_gap"])
    except Exception as e:
        print(f"Yahoo Finance headlines fetch failed: {e}")
    return headlines


def fetch_all_headlines(max_age_hours=24):
    """Collect all free sources and keep only items inside the live window.

    Undated items are kept only when the window reaches the present
    (date_window.in_window rule). Older dated RSS noise is dropped so
    sentiment/market agents are not skewed by stale headlines.
    """
    raw = (
        fetch_rss_headlines()
        + fetch_google_news_headlines()
        + fetch_telegram_headlines()
        + fetch_reddit_headlines()
        + fetch_stocktwits_headlines()
        + fetch_yahoo_headlines()
    )
    start_dt, end_dt, start_date, end_date = recent_window_hours(max_age_hours)
    kept, observed = filter_items_to_window(raw, start_dt=start_dt, end_dt=end_dt)
    print(
        f"News window {start_date}..{end_date}: {len(raw)} raw -> {len(kept)} in-window "
        f"({len(observed)} dated)"
    )
    return kept
