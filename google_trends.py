"""Best-effort Google Trends collector. Stores daily normalized interest history."""
import json
import os
from datetime import datetime, timezone

HISTORY_PATH = os.path.join("history", "google_trends.json")
KEYWORDS = ["Bitcoin", "BTC", "crypto"]


def _load():
    if not os.path.exists(HISTORY_PATH):
        return {"updated_at": None, "source": "Google Trends via pytrends", "keywords": KEYWORDS, "series": []}
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated_at": None, "source": "Google Trends via pytrends", "keywords": KEYWORDS, "series": []}


def collect_daily(force=False):
    now = datetime.now(timezone.utc)
    data = _load()
    today = now.date().isoformat()
    if not force and data.get("series") and data["series"][-1].get("date") == today:
        return data
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=0, retries=1, backoff_factor=0.2)
        pytrends.build_payload(KEYWORDS, timeframe="today 3-m", geo="")
        frame = pytrends.interest_over_time()
        if frame is None or frame.empty:
            raise RuntimeError("Google Trends returned no observations")
        row = frame.iloc[-1]
        values = {key: int(row[key]) for key in KEYWORDS if key in row and row[key] == row[key]}
        if not values:
            raise RuntimeError("Google Trends row had no keyword values")
        data["series"] = [x for x in data.get("series", []) if x.get("date") != today]
        data["series"].append({"date": today, "values": values})
        data["series"] = data["series"][-120:]
        data["updated_at"] = now.isoformat()
        data["status"] = "ok"
    except Exception as exc:
        data["status"] = "unavailable"
        data["error"] = f"{type(exc).__name__}: {exc}"
        data["updated_at"] = now.isoformat()
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


if __name__ == "__main__":
    print(json.dumps(collect_daily(), indent=2, ensure_ascii=False))
