"""
agents/news_sentiment.py
------------------------
Basit, kural-tabanli haber sentiment skoru.

Mevcut headlines listesini alir ve -100 (cok bearish) ile
+100 (cok bullish) arasinda bir skor dondurur.

Yontem: Anahtar kelime eslestirme + basit agirliklar.
LLM veya dis API kullanmaz.
"""

import re

# Higher weight = stronger signal
BULLISH_KEYWORDS = {
    # strong
    r"\ball[- ]?time high\b": 3,
    r"\bath\b": 2.5,
    r"\bbull\s*run\b": 3,
    r"\bbullish\b": 2.5,
    r"\bsurge[sd]?\b": 2.5,
    r"\brally\b": 2.2,
    r"\bsoar(ed|ing)?\b": 2.5,
    r"\brecord high\b": 2.8,
    r"\bbreakout\b": 2.2,
    r"\bapproval\b": 2.0,
    r"\betf\s*(inflow|approved|launch)": 2.5,
    r"\binstitutional\s*(buying|interest|adoption)": 2.3,
    r"\baccumulation\b": 2.0,
    r"\bbuy\s*the\s*dip\b": 1.8,
    r"\brecovery\b": 1.8,
    r"\buptrend\b": 2.0,
    r"\bmoon\b": 1.5,
    r"\bpump\b": 1.5,
    # medium
    r"\bgain[s]?\b": 1.2,
    r"\brise[sd]?\b": 1.2,
    r"\bjump(ed|s)?\b": 1.3,
    r"\bpositive\b": 1.1,
    r"\boptimistic\b": 1.4,
    r"\badoption\b": 1.3,
    r"\bpartnership\b": 1.2,
}

BEARISH_KEYWORDS = {
    # strong
    r"\bcrash(ed|ing)?\b": 3.0,
    r"\bcollapse[sd]?\b": 2.8,
    r"\bplunge[sd]?\b": 2.7,
    r"\btumble[sd]?\b": 2.5,
    r"\bbear\s*market\b": 2.8,
    r"\bbearish\b": 2.5,
    r"\bsell[- ]?off\b": 2.5,
    r"\bcapitulation\b": 2.8,
    r"\bliquidat(e|ed|ion|ions)\b": 2.3,
    r"\bhack(ed|ing)?\b": 2.2,
    r"\bscam\b": 2.0,
    r"\bfraud\b": 2.0,
    r"\bbankrupt(cy)?\b": 2.5,
    r"\bsec\s*(lawsuit|charges|sues)": 2.3,
    r"\bbanned?\b": 2.0,
    r"\brecession\b": 2.2,
    r"\bfear\b": 1.8,
    r"\bpanic\b": 2.3,
    r"\bdump\b": 1.8,
    # medium
    r"\bdrop(ped|s)?\b": 1.4,
    r"\bfall(s|en|ing)?\b": 1.3,
    r"\bdecline[sd]?\b": 1.4,
    r"\bweak(ness)?\b": 1.3,
    r"\bdowntrend\b": 1.8,
    r"\bcorrection\b": 1.5,
    r"\brisk[- ]?off\b": 1.6,
    r"\boutflow\b": 1.5,
}


def score_headlines(headlines):
    """
    headlines: list of dicts with at least a 'headline' key
               (as produced by news_sources / latest_news.json)

    Returns:
        {
            "score": float (-100 to +100),
            "label": str,
            "bullish_hits": int,
            "bearish_hits": int,
            "total_headlines": int,
            "sample_bullish": list[str],
            "sample_bearish": list[str],
        }
    """
    if not headlines:
        return {
            "score": 0.0,
            "label": "No data",
            "bullish_hits": 0,
            "bearish_hits": 0,
            "total_headlines": 0,
            "sample_bullish": [],
            "sample_bearish": [],
        }

    bullish_score = 0.0
    bearish_score = 0.0
    bullish_hits = 0
    bearish_hits = 0
    sample_bullish = []
    sample_bearish = []

    for item in headlines:
        text = (item.get("headline") or "").lower()
        if not text or len(text) < 10:
            continue

        item_bull = 0.0
        item_bear = 0.0

        for pattern, weight in BULLISH_KEYWORDS.items():
            if re.search(pattern, text, re.IGNORECASE):
                item_bull += weight
                bullish_hits += 1

        for pattern, weight in BEARISH_KEYWORDS.items():
            if re.search(pattern, text, re.IGNORECASE):
                item_bear += weight
                bearish_hits += 1

        bullish_score += item_bull
        bearish_score += item_bear

        if item_bull > item_bear and len(sample_bullish) < 3:
            sample_bullish.append(item.get("headline", "")[:120])
        elif item_bear > item_bull and len(sample_bearish) < 3:
            sample_bearish.append(item.get("headline", "")[:120])

    raw = bullish_score - bearish_score
    # Normalize roughly by number of headlines so score stays in useful range
    n = max(len(headlines), 1)
    normalized = (raw / n) * 18  # scaling factor tuned for typical headline volume
    score = max(-100.0, min(100.0, normalized))

    if score >= 35:
        label = "Bullish"
    elif score >= 12:
        label = "Slightly Bullish"
    elif score <= -35:
        label = "Bearish"
    elif score <= -12:
        label = "Slightly Bearish"
    else:
        label = "Neutral"

    return {
        "score": round(score, 1),
        "label": label,
        "bullish_hits": bullish_hits,
        "bearish_hits": bearish_hits,
        "total_headlines": len(headlines),
        "sample_bullish": sample_bullish,
        "sample_bearish": sample_bearish,
    }


def load_and_score(history_dir="history"):
    """Convenience: load history/latest_news.json and return sentiment."""
    import os
    import json

    path = os.path.join(history_dir, "latest_news.json")
    if not os.path.exists(path):
        return score_headlines([])

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        headlines = data.get("headlines", [])
        return score_headlines(headlines)
    except Exception:
        return score_headlines([])
