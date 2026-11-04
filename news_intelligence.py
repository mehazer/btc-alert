"""Crypto news intelligence: category tagging, ranking, deduplication and optional AI summary."""
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from collections import defaultdict

CATEGORY_RULES = {
    "Market & Macro": [r"bitcoin", r"btc", r"crypto market", r"risk[- ]off", r"stocks", r"macro", r"dollar", r"yield", r"inflation", r"cpi", r"fed", r"fomc", r"rate"],
    "Bitcoin & ETF": [r"bitcoin", r"\bbtc\b", r"etf", r"halving", r"microstrategy", r"saylor", r"mining"],
    "Regulation & Politics": [r"sec", r"cftc", r"regulat", r"congress", r"election", r"government", r"law", r"ban", r"legal", r"policy"],
    "Security & Hacks": [r"hack", r"exploit", r"attack", r"stolen", r"launder", r"breach", r"vulnerab", r"scam", r"fraud"],
    "DeFi & Stablecoins": [r"defi", r"stablecoin", r"usdt", r"usdc", r"lending", r"liquidity", r"dex", r"yield", r"bridge"],
    "Ethereum & L2": [r"ethereum", r"\beth\b", r"layer.?2", r"l2", r"rollup", r"arbitrum", r"optimism", r"base", r"pectra", r"fusaka"],
    "Altcoins & Projects": [r"solana", r"\bsol\b", r"zcash", r"\bzec\b", r"zama", r"rune", r"xrp", r"cardano", r"polygon", r"upgrade", r"mainnet", r"listing"],
    "Institutional & Exchanges": [r"binance", r"coinbase", r"kraken", r"exchange", r"institution", r"bank", r"custody", r"tokeniz", r"trading"],
}
CATEGORY_PRIORITY = ["Security & Hacks", "Regulation & Politics", "Bitcoin & ETF", "Market & Macro", "Institutional & Exchanges", "DeFi & Stablecoins", "Ethereum & L2", "Altcoins & Projects"]
SOURCE_WEIGHT = {"coindesk": 1.4, "the block": 1.35, "reuters": 1.5, "bloomberg": 1.5, "cointelegraph": 1.15, "decrypt": 1.1, "google news": 1.0, "reddit": .55, "telegram": .65}


def _published_ts(item):
    raw = item.get("published") or item.get("time") or item.get("date")
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw, timezone.utc)
        try:
            dt = parsedate_to_datetime(str(raw))
        except Exception:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def categorize(text):
    low = (text or "").lower()
    scores = {cat: sum(bool(re.search(p, low)) for p in pats) for cat, pats in CATEGORY_RULES.items()}
    best = max(scores, key=scores.get) if scores else "Other"
    if scores.get(best, 0) == 0:
        return "Other"
    return best


def enrich_and_rank(headlines, limit=60):
    now = datetime.now(timezone.utc)
    seen = set()
    out = []
    for item in headlines or []:
        headline = (item.get("headline") or "").strip()
        key = _norm(headline)
        if len(key) < 12 or key in seen:
            continue
        seen.add(key)
        published = _published_ts(item)
        age_hours = max(0.0, (now - published).total_seconds() / 3600) if published else 24.0
        source = (item.get("source") or "").lower()
        source_weight = next((v for k, v in SOURCE_WEIGHT.items() if k in source), 1.0)
        category = item.get("category") or categorize(headline)
        urgency = max(0.15, 1.0 - min(age_hours, 48.0) / 72.0)
        importance = round(source_weight * urgency + (0.9 if category in CATEGORY_PRIORITY[:4] else 0.25), 3)
        record = dict(item)
        record.update({"category": category, "importance": importance, "age_hours": round(age_hours, 1)})
        out.append(record)
    out.sort(key=lambda x: (-x["importance"], x.get("published", "")), reverse=False)
    return out[:limit]


def category_digest(headlines, max_items=5):
    groups = defaultdict(list)
    for item in headlines:
        groups[item.get("category", "Other")].append(item)
    digest = []
    for category in CATEGORY_PRIORITY + ["Other"]:
        items = groups.get(category, [])
        if not items:
            continue
        digest.append({
            "category": category,
            "count": len(items),
            "headlines": items[:max_items],
            "top_sources": sorted({i.get("source", "Unknown") for i in items})[:5],
        })
    return digest


def _fallback_interpretation(digests):
    important = [x for d in digests for x in d["headlines"][:2]]
    security = sum(1 for x in important if x.get("category") == "Security & Hacks")
    regulation = sum(1 for x in important if x.get("category") == "Regulation & Politics")
    bitcoin = sum(1 for x in important if x.get("category") == "Bitcoin & ETF")
    if security:
        tone = "Caution"
        summary = "Security-related headlines are prominent; verify affected protocols and whether contagion is material."
    elif bitcoin >= 2:
        tone = "BTC-focused"
        summary = "The news flow is centered on Bitcoin and market direction; distinguish fresh catalysts from price recap headlines."
    elif regulation:
        tone = "Policy-sensitive"
        summary = "Regulatory and political headlines dominate; market impact depends on jurisdiction and implementation details."
    else:
        tone = "Mixed"
        summary = "The news flow is mixed and should be read alongside price, volume, funding, and open interest."
    return {"status": "fallback", "tone": tone, "summary": summary, "key_points": [x.get("headline", "") for x in important[:5]], "updated_at": datetime.now(timezone.utc).isoformat()}


def ai_interpret(digests):
    api_key = os.environ.get("NEWS_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base = (os.environ.get("NEWS_AI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("NEWS_AI_MODEL", "gpt-4o-mini")
    fallback = _fallback_interpretation(digests)
    if not api_key:
        fallback["status"] = "unavailable"
        fallback["reason"] = "NEWS_AI_API_KEY is not configured"
        return fallback
    compact = [{"category": d["category"], "headlines": [x.get("headline") for x in d["headlines"][:4]]} for d in digests]
    prompt = {"categories": compact, "instructions": "Summarize the latest crypto news for a dashboard. Do not invent facts. Return JSON with tone, summary, key_points, market_implications, watch_items. Keep summary under 320 characters, key_points and watch_items to at most 4 short strings."}
    try:
        import requests
        resp = requests.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "messages": [{"role": "system", "content": "You are a cautious crypto-news analyst. Output valid JSON only."}, {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}], "temperature": 0.1, "max_tokens": 900}, timeout=45)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```json\s*|\s*```$", "", content.strip())
        result = json.loads(content)
        result.update({"status": "ai", "model": model, "updated_at": datetime.now(timezone.utc).isoformat()})
        return result
    except Exception as exc:
        fallback["reason"] = f"AI interpretation failed: {type(exc).__name__}"
        return fallback


def build_intelligence(headlines, sources):
    ranked = enrich_and_rank(headlines)
    digests = category_digest(ranked)
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "source_count": len(sources or []), "headline_count": len(ranked), "categories": digests, "ai": ai_interpret(digests), "method": "deduplicated, source-weighted, recency-ranked; optional AI layer"}
