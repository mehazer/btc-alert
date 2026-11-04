"""Hourly crypto news intelligence runner."""
import json
import os
from datetime import datetime, timezone

import google_trends
import news_intelligence
import news_sources

HISTORY_DIR = "history"


def save_json(name, payload):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(os.path.join(HISTORY_DIR, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def main():
    headlines = news_sources.fetch_all_headlines()
    sources = news_sources.list_all_sources()
    intelligence = news_intelligence.build_intelligence(headlines, sources)
    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
        "headline_count": intelligence["headline_count"],
        "headlines": news_intelligence.enrich_and_rank(headlines),
        "categories": intelligence["categories"],
        "ai": intelligence["ai"],
        "method": intelligence["method"],
    }
    save_json("latest_news.json", snapshot)
    trends = google_trends.collect_daily()
    print(f"{len(headlines)} raw headlines -> {intelligence['headline_count']} ranked unique headlines")
    print(f"Categories: {', '.join(x['category'] for x in intelligence['categories']) or 'none'}")
    print(f"AI news interpretation: {intelligence['ai'].get('status')} ({intelligence['ai'].get('tone', 'n/a')})")
    print(f"Google Trends: {trends.get('status')} | {trends.get('updated_at')}")


if __name__ == "__main__":
    main()
