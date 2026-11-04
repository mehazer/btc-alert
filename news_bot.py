"""Hourly crypto news intelligence runner."""
import json
import os
from datetime import datetime, timezone

import requests

import google_trends
import news_intelligence
import news_sources

HISTORY_DIR = "history"


def save_json(name, payload):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(os.path.join(HISTORY_DIR, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def telegram_news_message(intelligence):
    """Build a compact hourly Telegram summary from the ranked news digest."""
    ai = intelligence.get("ai") or {}
    lines = [
        "📰 CRYPTO NEWS UPDATE",
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC | {intelligence.get('headline_count', 0)} headlines",
        "",
        f"Tone: {ai.get('tone', 'Mixed')}",
        f"{ai.get('summary', 'News flow should be read with price, volume, OI and funding.')}",
    ]
    points = ai.get("key_points") or []
    if points:
        lines.append("")
        lines.append("KEY NEWS")
        lines.extend(f"• {str(point)[:220]}" for point in points[:4])
    categories = intelligence.get("categories") or []
    if categories:
        lines.append("")
        lines.append("CATEGORIES")
        for category in categories[:5]:
            headline = (category.get("headlines") or [{}])[0].get("headline", "")
            lines.append(f"• {category.get('category')}: {str(headline)[:180]}")
    return "\n".join(lines)[:3900]


def send_telegram_news(intelligence):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram news notification skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": telegram_news_message(intelligence)},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API rejected message: {payload}")
    print("Telegram news notification sent")
    return True


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
    try:
        send_telegram_news(intelligence)
    except Exception as exc:
        print(f"Telegram news notification failed (history still saved): {type(exc).__name__}: {exc}")
    print(f"{len(headlines)} raw headlines -> {intelligence['headline_count']} ranked unique headlines")
    print(f"Categories: {', '.join(x['category'] for x in intelligence['categories']) or 'none'}")
    print(f"AI news interpretation: {intelligence['ai'].get('status')} ({intelligence['ai'].get('tone', 'n/a')})")
    print(f"Google Trends: {trends.get('status')} | {trends.get('updated_at')}")


if __name__ == "__main__":
    main()
