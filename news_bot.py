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


def _impact_icon(direction, tier):
    d = (direction or "Neutral").lower()
    t = (tier or "LOW").upper()
    if d.startswith("bull"):
        base = "🟢"
    elif d.startswith("bear"):
        base = "🔴"
    else:
        base = "🟡"
    if t == "HIGH":
        return base + "⚠️"
    return base


def _rank_headlines(intelligence):
    """Build impact-ranked items from categories/headlines when present."""
    items = []
    seen = set()
    for category in intelligence.get("categories") or []:
        cat = category.get("category") or "General"
        for h in category.get("headlines") or []:
            title = " ".join(str(h.get("headline") or h.get("title") or "").split())
            if not title:
                continue
            key = title.lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            direction = h.get("direction") or h.get("bias") or "Neutral"
            tier = (h.get("impact_tier") or h.get("impact") or "").upper()
            if tier not in {"HIGH", "MEDIUM", "LOW"}:
                if cat in ("Security & Hacks", "Regulation & Politics"):
                    tier = "HIGH"
                elif cat in ("Bitcoin & ETF", "Market & Macro", "Institutional & Exchanges"):
                    tier = "MEDIUM"
                else:
                    tier = "LOW"
            score = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(tier, 1)
            items.append({
                "title": title,
                "category": cat,
                "direction": direction,
                "tier": tier,
                "score": score,
                "summary": h.get("summary") or h.get("ai_summary") or "",
                "impact_note": h.get("impact_note") or h.get("why") or f"Ranked under {cat}.",
            })
    for point in (intelligence.get("ai") or {}).get("key_points") or []:
        clean = " ".join(str(point).split())
        if not clean:
            continue
        key = clean.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "title": clean,
            "category": "AI digest",
            "direction": (intelligence.get("ai") or {}).get("tone") or "Neutral",
            "tier": "MEDIUM",
            "score": 2,
            "summary": "",
            "impact_note": "From AI key points digest.",
        })
    items.sort(key=lambda x: (-x["score"], x["title"]))
    return items


def telegram_news_message(intelligence):
    """Impact-ranked Telegram news: summary, direction, tier, 24h focus."""
    ai = intelligence.get("ai") or {}
    tone = ai.get("tone") or "Mixed"
    tone_icon = "🟢" if str(tone).lower().startswith("bull") else ("🔴" if str(tone).lower().startswith("bear") else "🟡")
    lines = [
        "📰 CRYPTO NEWS UPDATE",
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC · {intelligence.get('headline_count', 0)} headlines",
        "",
        f"{tone_icon} Market news tone: {tone}",
        f"🔵 {ai.get('summary', 'Read news with price, volume, OI and funding — not alone.')}",
        "",
        "📌 LAST 24H — top impact",
    ]
    ranked = _rank_headlines(intelligence)
    top = ranked[:5]
    if not top:
        lines.append("• No ranked headlines available this run.")
    for i, item in enumerate(top, 1):
        icon = _impact_icon(item["direction"], item["tier"])
        lines.append(f"{i}. {icon} [{item['tier']}] {item['title'][:160]}")
        lines.append(f"   {item['direction']} · {item['category']}")
        note = (item.get("impact_note") or "")[:140]
        if note:
            lines.append(f"   Why: {note}")
    weekly = intelligence.get("weekly_top") or intelligence.get("last_7d") or []
    lines.append("")
    lines.append("📅 LAST 7D — top impact")
    if weekly:
        for i, item in enumerate(weekly[:5], 1):
            title = item.get("title") or item.get("headline") or str(item)
            lines.append(f"{i}. {title[:180]}")
    else:
        lines.append("• Weekly digest not built this hour — 24h list is authoritative.")
        lines.append("• Avoid repeating the same 24h items as weekly without new developments.")
    lines.append("")
    lines.append("Note: Impact tiers are interpretive, not guarantees.")
    return "\n".join(lines)[:3900]


def send_telegram_news(intelligence):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram news notification skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = telegram_news_message(intelligence)
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=30)
        print("Telegram news status:", r.status_code)
        return r.ok
    except Exception as e:
        print("Telegram news error:", e)
        return False


def main():
    print("News bot", datetime.now(timezone.utc).isoformat())
    records = news_sources.collect_all()
    intelligence = news_intelligence.build_intelligence(records)
    save_json("latest_news.json", intelligence)
    append_json("news_runs.json", {
        "ts": datetime.now(timezone.utc).isoformat(),
        "headline_count": intelligence.get("headline_count"),
        "method": intelligence.get("method"),
    })
    try:
        trends = google_trends.fetch_trends()
        if trends:
            save_json("google_trends.json", trends)
    except Exception as e:
        print("trends", e)
    ok = send_telegram_news(intelligence)
    print("Telegram news sent" if ok else "Telegram news not sent")
    print("Done.")


if __name__ == "__main__":
    main()
