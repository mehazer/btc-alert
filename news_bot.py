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


def append_json(name, record):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        rows = []
    rows.append(record)
    save_json(name, rows)


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
    seen = set()
    if points:
        lines.append("")
        lines.append("KEY NEWS")
        for point in points[:4]:
            clean = " ".join(str(point).split())
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                lines.append(f"• {clean[:220]}")
    categories = intelligence.get("categories") or []
    if categories:
        lines.append("")
        lines.append("CATEGORIES")
        for category in categories[:5]:
            headline = (category.get("headlines") or [{}])[0].get("headline", "")
            clean = " ".join(str(headline).split())
            if clean.lower() in seen:
                continue
            seen.add(clean.lower())
            lines.append(f"• {category.get('category')}: {clean[:180]}")
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
        "quality": intelligence.get("quality", {}),
        "method": intelligence["method"],
    }
    save_json("latest_news.json", snapshot)
    trends = google_trends.collect_daily()
    append_json("news_runs.json", {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "updated_at": snapshot["updated_at"],
        "raw_headlines": len(headlines),
        "unique_headlines": intelligence["headline_count"],
        "source_count": len(sources),
        "category_count": len(intelligence["categories"]),
        "ai_status": intelligence["ai"].get("status"),
        "trends_status": trends.get("status"),
    })
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
