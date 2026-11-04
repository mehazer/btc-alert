"""
agents/technical_extras.py
--------------------------
Free technical inputs beyond price/OI/funding:
  - Binance global + top-trader long/short ratios (accounts + positions)
  - Liquidations (edgeornot, Binance+Bybit)
  - Bitcoin halving cycle (days passed / days left)
  - Direction labels (Rising / Falling / Flat)

All fetches are best-effort; failures return None fields so the bot keeps running.
"""

from datetime import datetime, timezone, timedelta
import requests

BINANCE_LS_BASE = "https://fapi.binance.com/futures/data"
EDGEORNOT_LIQ = "https://edgeornot.com/api/liquidations"
MEMPOOL_HEIGHT = "https://mempool.space/api/blocks/tip/height"

LAST_HALVING_BLOCK = 840_000
LAST_HALVING_DATE = datetime(2024, 4, 20, tzinfo=timezone.utc)
NEXT_HALVING_BLOCK = 1_050_000
AVG_BLOCK_MINUTES = 10.0

BINANCE_LS_SYMBOLS = {
    "BTCUSDT": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
    "SOLUSDT": "SOLUSDT",
    "ZECUSDT": "ZECUSDT",
}

EDGEORNOT_COINS = {
    "BTCUSDT": "btc",
    "ETHUSDT": "eth",
    "SOLUSDT": "sol",
    "ZECUSDT": "zec",
}


def _direction(current, previous, deadband_pct=0.5):
    if current is None or previous is None:
        return "Unknown"
    if previous == 0:
        if current > 0:
            return "Rising"
        if current < 0:
            return "Falling"
        return "Flat"
    chg = ((current - previous) / abs(previous)) * 100
    if chg > deadband_pct:
        return "Rising"
    if chg < -deadband_pct:
        return "Falling"
    return "Flat"


def _pct_change(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return round(((current - previous) / abs(previous)) * 100, 3)


def _get_json(url, params=None, timeout=12):
    resp = requests.get(
        url,
        params=params or {},
        timeout=timeout,
        headers={"User-Agent": "btc-alert/1.0"},
    )
    resp.raise_for_status()
    return resp.json()


def fetch_binance_ls_ratios(symbol, period="4h", limit=5):
    mapped = BINANCE_LS_SYMBOLS.get(symbol.upper())
    if not mapped:
        return {"available": False, "reason": f"No Binance L/S mapping for {symbol}"}

    out = {
        "available": True,
        "symbol": mapped,
        "period": period,
        "global_ls_ratio": None,
        "global_long_account": None,
        "global_short_account": None,
        "global_ls_dir": "Unknown",
        "top_account_ls_ratio": None,
        "top_account_ls_dir": "Unknown",
        "top_position_ls_ratio": None,
        "top_position_ls_dir": "Unknown",
        "error": None,
    }

    try:
        g = _get_json(
            f"{BINANCE_LS_BASE}/globalLongShortAccountRatio",
            {"symbol": mapped, "period": period, "limit": limit},
        )
        if isinstance(g, list) and g:
            last, prev = g[-1], g[-2] if len(g) >= 2 else None
            out["global_ls_ratio"] = float(last.get("longShortRatio"))
            out["global_long_account"] = float(last.get("longAccount"))
            out["global_short_account"] = float(last.get("shortAccount"))
            if prev:
                out["global_ls_dir"] = _direction(
                    out["global_ls_ratio"], float(prev.get("longShortRatio")), deadband_pct=1.0
                )
                out["global_ls_chg_pct"] = _pct_change(
                    out["global_ls_ratio"], float(prev.get("longShortRatio"))
                )
    except Exception as e:
        out["error"] = f"global L/S: {e}"

    try:
        t = _get_json(
            f"{BINANCE_LS_BASE}/topLongShortAccountRatio",
            {"symbol": mapped, "period": period, "limit": limit},
        )
        if isinstance(t, list) and t:
            last, prev = t[-1], t[-2] if len(t) >= 2 else None
            out["top_account_ls_ratio"] = float(last.get("longShortRatio"))
            if prev:
                out["top_account_ls_dir"] = _direction(
                    out["top_account_ls_ratio"], float(prev.get("longShortRatio")), deadband_pct=1.0
                )
                out["top_account_ls_chg_pct"] = _pct_change(
                    out["top_account_ls_ratio"], float(prev.get("longShortRatio"))
                )
    except Exception as e:
        out["error"] = (out.get("error") or "") + f" | top account L/S: {e}"

    try:
        p = _get_json(
            f"{BINANCE_LS_BASE}/topLongShortPositionRatio",
            {"symbol": mapped, "period": period, "limit": limit},
        )
        if isinstance(p, list) and p:
            last, prev = p[-1], p[-2] if len(p) >= 2 else None
            out["top_position_ls_ratio"] = float(last.get("longShortRatio"))
            if prev:
                out["top_position_ls_dir"] = _direction(
                    out["top_position_ls_ratio"], float(prev.get("longShortRatio")), deadband_pct=1.0
                )
                out["top_position_ls_chg_pct"] = _pct_change(
                    out["top_position_ls_ratio"], float(prev.get("longShortRatio"))
                )
    except Exception as e:
        out["error"] = (out.get("error") or "") + f" | top position L/S: {e}"

    return out


def fetch_liquidations(symbol, hours=24):
    coin = EDGEORNOT_COINS.get(symbol.upper())
    if not coin:
        return {"available": False, "reason": f"No liquidation mapping for {symbol}"}

    try:
        data = _get_json(EDGEORNOT_LIQ, {"coin": coin, "hours": hours})
        total = data.get("total") or {}
        long_usd = float(total.get("long") or 0)
        short_usd = float(total.get("short") or 0)
        bias = "Balanced"
        if long_usd > short_usd * 1.35:
            bias = "Long flush dominant"
        elif short_usd > long_usd * 1.35:
            bias = "Short flush dominant"
        return {
            "available": True,
            "coin": coin,
            "hours": hours,
            "long_usd": round(long_usd, 0),
            "short_usd": round(short_usd, 0),
            "total_usd": round(long_usd + short_usd, 0),
            "bias": bias,
            "by_exchange": data.get("by_exchange"),
            "updated": data.get("updated"),
        }
    except Exception as e:
        return {"available": False, "reason": str(e)}


def compute_halving(use_live_height=True):
    now = datetime.now(timezone.utc)
    height = None
    if use_live_height:
        try:
            height = int(_get_json(MEMPOOL_HEIGHT))
        except Exception:
            height = None

    if height is not None:
        blocks_since = max(0, height - LAST_HALVING_BLOCK)
        blocks_to_next = max(0, NEXT_HALVING_BLOCK - height)
        days_passed = blocks_since * AVG_BLOCK_MINUTES / (60 * 24)
        days_left = blocks_to_next * AVG_BLOCK_MINUTES / (60 * 24)
        cycle_total_blocks = NEXT_HALVING_BLOCK - LAST_HALVING_BLOCK
        cycle_pct = min(100.0, max(0.0, 100.0 * blocks_since / cycle_total_blocks))
        source = f"live height {height}"
    else:
        days_passed = (now - LAST_HALVING_DATE).total_seconds() / 86400
        days_left = max(0.0, 1458.0 - days_passed)
        cycle_pct = min(100.0, max(0.0, 100.0 * days_passed / 1458.0))
        blocks_since = None
        blocks_to_next = None
        source = "date estimate"

    return {
        "last_halving": LAST_HALVING_DATE.date().isoformat(),
        "next_halving_block": NEXT_HALVING_BLOCK,
        "current_height": height,
        "blocks_since_last": blocks_since,
        "blocks_to_next": blocks_to_next,
        "days_passed": round(days_passed, 1),
        "days_left": round(days_left, 1),
        "cycle_pct": round(cycle_pct, 1),
        "source": source,
        "as_of": now.isoformat(),
    }


def series_directions(history, current_price=None, current_oi=None, current_volume=None,
                      current_funding=None, current_rsi=None):
    if not history:
        history = []
    prev = history[-1] if history else {}
    if current_price is None and len(history) >= 2:
        current_price = history[-1].get("price")
        prev = history[-2]
    if current_oi is None and len(history) >= 2:
        current_oi = history[-1].get("open_interest")
    if current_volume is None and len(history) >= 2:
        current_volume = history[-1].get("volume")
    if current_funding is None and len(history) >= 2:
        current_funding = history[-1].get("funding_rate")
    if current_rsi is None and len(history) >= 2:
        current_rsi = history[-1].get("rsi")

    def d(cur, key, deadband=0.5):
        p = prev.get(key)
        return _direction(cur, p, deadband_pct=deadband), _pct_change(cur, p)

    price_dir, price_chg = d(current_price, "price", 0.15)
    oi_dir, oi_chg = d(current_oi, "open_interest", 0.4)
    vol_dir, vol_chg = d(current_volume, "volume", 5.0)
    fund_dir, fund_chg = d(current_funding, "funding_rate", 8.0)
    rsi_dir, rsi_chg = d(current_rsi, "rsi", 1.5)

    return {
        "price_dir": price_dir,
        "price_chg_pct": price_chg,
        "oi_dir": oi_dir,
        "oi_chg_pct": oi_chg,
        "volume_dir": vol_dir,
        "volume_chg_pct": vol_chg,
        "funding_dir": fund_dir,
        "funding_chg_pct": fund_chg,
        "rsi_dir": rsi_dir,
        "rsi_chg_pct": rsi_chg,
    }


def enrich_symbol_technicals(symbol, history, current, period="4h"):
    dirs = series_directions(
        history[:-1] if history else [],
        current_price=current.get("price"),
        current_oi=current.get("open_interest"),
        current_volume=current.get("volume"),
        current_funding=current.get("funding_rate"),
        current_rsi=current.get("rsi"),
    )
    ls = fetch_binance_ls_ratios(symbol, period=period, limit=5)
    liq = fetch_liquidations(symbol, hours=24)
    return {
        "directions": dirs,
        "long_short": ls,
        "liquidations": liq,
    }
