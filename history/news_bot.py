"""
news_bot.py
------------------------------
Haber toplama isini teknik (RSI) botundan bagimsiz, kendi
zamanlamasinda calistirir. Sonuclari history/latest_news.json'a
yazar (kaynak listesi + cekilen basliklar + zaman damgasi).
"""

import os
import json
from datetime import datetime, timezone

import news_sources

HISTORY_DIR = "history"


def save_latest_news(headlines, sources):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, "latest_news.json")
    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "headlines": headlines,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)


def main():
    headlines = news_sources.fetch_all_headlines()
    sources = news_sources.list_all_sources()
    print(f"{len(headlines)} haber cekildi, {len(sources)} kaynaktan.")
    for h in headlines:
        print(f"[{h['source']}] {h['headline']}")
    save_latest_news(headlines, sources)


if __name__ == "__main__":
    main()
