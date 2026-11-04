"""
agents/news_sentiment.py
------------------------
Rule-based news sentiment with:
  - Source weighting (including Google News)
  - Macro vs coin-specific scores
  - Catalyst tags (ZEC NU7, upgrades, listings, hacks, etc.)
  - Shorter-window urgency when timestamps available

Score range: -100 (very bearish) … +100 (very bullish)
"""

import re
from datetime import datetime, timezone, timedelta

SOURCE_WEIGHTS = {
    "coindesk": 1.4,
    "the block": 1.35,
    "cointelegraph": 1.15,
    "decrypt": 1.1,
    "bloomberg": 1.5,
    "reuters": 1.5,
    "wsj": 1.4,
    "ft": 1.3,
    "binance": 1.2,
    "official": 1.3,
    "google news": 1.25,
    "twitter": 0.85,
    "reddit": 0.75,
    "telegram": 0.7,
    "unknown": 1.0,
}

BULLISH_KEYWORDS = {
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
    r"\bgain[s]?\b": 1.2,
    r"\brise[sd]?\b": 1.2,
    r"\bjump(ed|s)?\b": 1.3,
    r"\bpositive\b": 1.1,
    r"\boptimistic\b": 1.4,
    r"\badoption\b": 1.3,
    r"\bpartnership\b": 1.2,
    r"\bupgrade\b": 1.8,
    r"\bmainnet\b": 1.6,
    r"\blaunch\b": 1.4,
}

BEARISH_KEYWORDS = {
    r"\bcrash(ed|ing)?\b": 3.0,
    r"\bcollapse[sd]?\b": 2.8,
    r"\bplunge[sd]?\b": 2.7,
    r"\btumble[sd]?\b": 2.5,
    r"\bbear\s*market\b": 2.8,
    r"\bbearish\b": 2.5,
    r"\bsell[- ]?off\b": 2.5,
    r"\bcapitulation\b": 2.8,
    r"\bliquidat(e|ed|ion|ions)\b": 2.3,
    r"\bhack(ed|ing)?\b": 2.4,
    r"\bscam\b": 2.0,
    r"\bfraud\b": 2.0,
    r"\bbankrupt(cy)?\b": 2.5,
    r"\bsec\s*(lawsuit|charges|sues)": 2.3,
    r"\bbanned?\b": 2.0,
    r"\brecession\b": 2.2,
    r"\bfear\b": 1.8,
    r"\bpanic\b": 2.3,
    r"\bdump\b": 1.8,
    r"\bdrop(ped|s)?\b": 1.4,
    r"\bfall(s|en|ing)?\b": 1.3,
    r"\bdecline[sd]?\b": 1.4,
    r"\bweak(ness)?\b": 1.3,
    r"\bdowntrend\b": 1.8,
    r"\bcorrection\b": 1.5,
    r"\brisk[- ]?off\b": 1.6,
    r"\boutflow\b": 1.5,
    r"\bsettlement\b": 1.4,
    r"\bdoj\b": 1.5,
}

CATALYST_PATTERNS = {
    "ZEC": [
        (r"\bnu7\b", "ZEC NU7 upgrade", 2.2),
        (r"\bzcash.*(upgrade|mainnet|hard\s*fork)", "ZEC network upgrade", 2.0),
        (r"\bshielded\b", "ZEC shielded activity", 1.3),
        (r"\bprivacy\s*coin", "Privacy-coin narrative", 1.2),
    ],
    "ZAMA": [
        (r"\bzama\b", "ZAMA mention", 1.5),
        (r"\bfhe\b|\bfully\s*homomorphic", "FHE / privacy compute", 1.8),
        (r"\bconfidential\s*(smart\s*contract|compute)", "Confidential contracts", 1.6),
    ],
    "BTC": [
        (r"\betf\s*(inflow|outflow)", "BTC ETF flow", 2.0),
        (r"\bmicrostrategy|saylor\b", "MicroStrategy / Saylor", 1.4),
        (r"\bhalving\b", "BTC halving narrative", 1.5),
    ],
    "ETH": [
        (r"\bethereum.*(upgrade|pectra|fusaka)", "ETH upgrade", 1.8),
        (r"\bstaking\s*(inflow|outflow|yield)", "ETH staking flow", 1.4),
    ],
    "MACRO": [
        (r"\bfed\b|\bfomc\b|\brate\s*cut|\brate\s*hike", "Fed / rates", 1.8),
        (r"\bmidterm|\belection\b|\bcongress\b", "US politics / election", 1.6),
        (r"\binflation|cpi\b|pce\b", "Inflation data", 1.5),
        (r"\bdoj\b|\bsec\b|\bregulation", "Regulation / enforcement", 1.5),
        (r"\bhack\b|\bexploit\b|\bbridge\s*hack", "Security incident", 2.0),
    ],
}


def _source_weight(source):
    if not source:
        return 1.0
    s = source.lower().strip()
    for key, w in SOURCE_WEIGHTS.items():
        if key in s:
            return w
    return 1.0


def _extract_catalysts(text):
    tags = []
    text_l = text.lower()
    for coin, patterns in CATALYST_PATTERNS.items():
        for pat, label, weight in patterns:
            if re.search(pat, text_l, re.IGNORECASE):
                tags.append({"coin": coin, "label": label, "weight": weight})
    return tags


def score_headlines(headlines, max_age_hours=None):
    if not headlines:
        return {
            "score": 0.0,
            "label": "No data",
            "macro_score": 0.0,
            "coin_scores": {},
            "bullish_hits": 0,
            "bearish_hits": 0,
            "total_headlines": 0,
            "sample_bullish": [],
            "sample_bearish": [],
            "catalysts": [],
            "top_catalysts": [],
        }

    now = datetime.now(timezone.utc)
    bullish_score = 0.0
    bearish_score = 0.0
    bullish_hits = 0
    bearish_hits = 0
    sample_bullish = []
    sample_bearish = []
    catalyst_counter = {}
    coin_bull = {}
    coin_bear = {}
    macro_bull = 0.0
    macro_bear = 0.0
    used = 0

    for item in headlines:
        text = (item.get("headline") or "").strip()
        if not text or len(text) < 10:
            continue

        if max_age_hours is not None:
            ts = item.get("published") or item.get("time") or item.get("date")
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    else:
                        # RFC 2822 from Google News pubDate, or ISO
                        from email.utils import parsedate_to_datetime
                        try:
                            dt = parsedate_to_datetime(str(ts))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                        except Exception:
                            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if (now - dt) > timedelta(hours=max_age_hours):
                        continue
                except Exception:
                    pass

        used += 1
        text_l = text.lower()
        sw = _source_weight(item.get("source"))

        item_bull = 0.0
        item_bear = 0.0

        for pattern, weight in BULLISH_KEYWORDS.items():
            if re.search(pattern, text_l, re.IGNORECASE):
                item_bull += weight
                bullish_hits += 1

        for pattern, weight in BEARISH_KEYWORDS.items():
            if re.search(pattern, text_l, re.IGNORECASE):
                item_bear += weight
                bearish_hits += 1

        cats = _extract_catalysts(text)
        for c in cats:
            key = c["label"]
            catalyst_counter[key] = catalyst_counter.get(key, 0) + c["weight"] * sw
            if c["coin"] == "MACRO":
                macro_bull += item_bull * sw * 0.5
                macro_bear += item_bear * sw * 0.5
            else:
                coin = c["coin"]
                coin_bull[coin] = coin_bull.get(coin, 0) + item_bull * sw
                coin_bear[coin] = coin_bear.get(coin, 0) + item_bear * sw

        if not cats:
            macro_bull += item_bull * sw * 0.3
            macro_bear += item_bear * sw * 0.3

        bullish_score += item_bull * sw
        bearish_score += item_bear * sw

        if item_bull > item_bear and len(sample_bullish) < 4:
            sample_bullish.append(text[:130])
        elif item_bear > item_bull and len(sample_bearish) < 4:
            sample_bearish.append(text[:130])

    n = max(used, 1)
    normalized = ((bullish_score - bearish_score) / n) * 16
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

    macro_raw = (macro_bull - macro_bear) / max(n * 0.4, 1)
    macro_score = max(-100.0, min(100.0, macro_raw * 20))

    coin_scores = {}
    for coin in set(list(coin_bull.keys()) + list(coin_bear.keys())):
        raw = (coin_bull.get(coin, 0) - coin_bear.get(coin, 0))
        coin_scores[coin] = round(max(-100.0, min(100.0, raw * 8)), 1)

    top_catalysts = sorted(
        [{"label": k, "weight": round(v, 2)} for k, v in catalyst_counter.items()],
        key=lambda x: -x["weight"],
    )[:8]

    return {
        "score": round(score, 1),
        "label": label,
        "macro_score": round(macro_score, 1),
        "coin_scores": coin_scores,
        "bullish_hits": bullish_hits,
        "bearish_hits": bearish_hits,
        "total_headlines": used,
        "sample_bullish": sample_bullish,
        "sample_bearish": sample_bearish,
        "catalysts": list(catalyst_counter.keys()),
        "top_catalysts": top_catalysts,
    }


def load_and_score(history_dir="history"):
    import os
    import json

    path = os.path.join(history_dir, "latest_news.json")
    if not os.path.exists(path):
        return score_headlines([])

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        headlines = data.get("headlines", [])
        return score_headlines(headlines, max_age_hours=12)
    except Exception:
        return score_headlines([])
