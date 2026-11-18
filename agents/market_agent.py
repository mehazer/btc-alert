"""
agents/market_agent.py
------------------------
Deterministik, kural-tabanli Market Agent (v2 - Directional).

AMAC:
Zaten toplanmis sinyalleri (RSI, Fear & Greed, Funding Rate,
OI degisimi, Fiyat degisimi, News Sentiment, optional social) tek bir
yonlu (directional) piyasa degerlendirmesine cevirir.

Optional signal_weights (from agent_calibration) scale each family.
Missing weights default to 1.0. Missing inputs stay null.
"""

from datetime import datetime, timezone


def _clamp(value, low, high):
    return max(low, min(high, value))


def _w(weights, key):
    if not weights:
        return 1.0
    try:
        return float(weights.get(key, 1.0))
    except (TypeError, ValueError):
        return 1.0


def evaluate(
    rsi=None,
    fear_greed_value=None,
    funding_rate=None,
    oi_change_pct=None,
    price_change_pct=None,
    news_sentiment=None,
    social_sentiment=None,
    signal_weights=None,
):
    """Directional market evaluation with optional calibrated weights."""
    bullish_points = 0.0
    bearish_points = 0.0
    reasons = []
    available = 0
    total_possible = 7
    weights = signal_weights or {}

    inputs = {
        "rsi": rsi,
        "fear_greed_value": fear_greed_value,
        "funding_rate": funding_rate,
        "oi_change_pct": oi_change_pct,
        "price_change_pct": price_change_pct,
        "news_sentiment": news_sentiment,
        "social_sentiment": social_sentiment,
    }

    if rsi is not None:
        available += 1
        w = _w(weights, "rsi")
        if rsi >= 75:
            bullish_points += 25 * w
            reasons.append(f"RSI yuksek ({rsi:.1f}) -> asiri alim baskisi")
        elif rsi >= 60:
            bullish_points += 12 * w
            reasons.append(f"RSI guclu ({rsi:.1f}) -> boğa egilimi")
        elif rsi <= 25:
            bearish_points += 25 * w
            reasons.append(f"RSI cok dusuk ({rsi:.1f}) -> asiri satim / korku")
        elif rsi <= 40:
            bearish_points += 12 * w
            reasons.append(f"RSI zayif ({rsi:.1f}) -> ayi egilimi")

    if fear_greed_value is not None:
        available += 1
        w = _w(weights, "fear_greed")
        if fear_greed_value >= 80:
            bullish_points += 20 * w
            reasons.append(f"Fear&Greed asiri acgozlu ({fear_greed_value})")
        elif fear_greed_value >= 60:
            bullish_points += 10 * w
            reasons.append(f"Fear&Greed iyimser ({fear_greed_value})")
        elif fear_greed_value <= 20:
            bearish_points += 20 * w
            reasons.append(f"Fear&Greed asiri korku ({fear_greed_value})")
        elif fear_greed_value <= 40:
            bearish_points += 10 * w
            reasons.append(f"Fear&Greed korku bolgesi ({fear_greed_value})")

    if funding_rate is not None:
        available += 1
        w = _w(weights, "funding")
        funding_pct = funding_rate * 100
        if funding_rate >= 0.0003:
            bullish_points += 22 * w
            reasons.append(f"Funding Rate yuksek (+{funding_pct:.4f}%) -> long'lar kalabalik")
        elif funding_rate >= 0.0001:
            bullish_points += 10 * w
            reasons.append(f"Funding Rate pozitif (+{funding_pct:.4f}%)")
        elif funding_rate <= -0.0003:
            bearish_points += 22 * w
            reasons.append(f"Funding Rate cok negatif ({funding_pct:.4f}%) -> short'lar kalabalik")
        elif funding_rate <= -0.0001:
            bearish_points += 10 * w
            reasons.append(f"Funding Rate negatif ({funding_pct:.4f}%)")

    if oi_change_pct is not None and price_change_pct is not None:
        available += 1
        w = _w(weights, "oi_price")
        if price_change_pct > 1.5 and oi_change_pct > 3:
            bullish_points += 18 * w
            reasons.append("Fiyat + OI birlikte yukseldi -> guclu boğa trendi")
        elif price_change_pct > 1.5 and oi_change_pct < -2:
            bearish_points += 8 * w
            reasons.append("Fiyat yukseldi ama OI dustu -> zayif ralli / short covering")
        elif price_change_pct < -1.5 and oi_change_pct > 3:
            bearish_points += 18 * w
            reasons.append("Fiyat dustu + OI artti -> guclu ayi baskisi")
        elif price_change_pct < -1.5 and oi_change_pct < -2:
            bullish_points += 8 * w
            reasons.append("Fiyat dustu + OI dustu -> long temizligi (potansiyel dibe yaklasma)")
    elif oi_change_pct is not None:
        available += 0.5
        if oi_change_pct > 8:
            reasons.append(f"OI belirgin artti (+{oi_change_pct:.1f}%)")
        elif oi_change_pct < -8:
            reasons.append(f"OI belirgin azaldi ({oi_change_pct:.1f}%)")

    if price_change_pct is not None:
        available += 0.5
        w = _w(weights, "price")
        if price_change_pct > 4:
            bullish_points += 8 * w
            reasons.append(f"Guclu fiyat yukselişi (+{price_change_pct:.1f}%)")
        elif price_change_pct < -4:
            bearish_points += 8 * w
            reasons.append(f"Guclu fiyat dususu ({price_change_pct:.1f}%)")

    if news_sentiment is not None:
        available += 1
        w = _w(weights, "news")
        if news_sentiment >= 40:
            bullish_points += 15 * w
            reasons.append(f"Haber sentimenti iyimser ({news_sentiment:+.0f})")
        elif news_sentiment >= 15:
            bullish_points += 7 * w
            reasons.append(f"Haber sentimenti hafif pozitif ({news_sentiment:+.0f})")
        elif news_sentiment <= -40:
            bearish_points += 15 * w
            reasons.append(f"Haber sentimenti kotumser ({news_sentiment:+.0f})")
        elif news_sentiment <= -15:
            bearish_points += 7 * w
            reasons.append(f"Haber sentimenti hafif negatif ({news_sentiment:+.0f})")

    if social_sentiment is not None:
        available += 1
        w = _w(weights, "social")
        if social_sentiment >= 40:
            bullish_points += 10 * w
            reasons.append(f"Sosyal sentiment iyimser ({social_sentiment:+.0f})")
        elif social_sentiment >= 15:
            bullish_points += 5 * w
            reasons.append(f"Sosyal sentiment hafif pozitif ({social_sentiment:+.0f})")
        elif social_sentiment <= -40:
            bearish_points += 10 * w
            reasons.append(f"Sosyal sentiment kotumser ({social_sentiment:+.0f})")
        elif social_sentiment <= -15:
            bearish_points += 5 * w
            reasons.append(f"Sosyal sentiment hafif negatif ({social_sentiment:+.0f})")

    net = bullish_points - bearish_points
    bias_score = int(_clamp(net * 1.4, -100, 100))
    intensity = int(_clamp(max(bullish_points, bearish_points), 0, 100))

    if bias_score >= 55 and intensity >= 45:
        state = "OVERHEATED"
    elif bias_score <= -55 and intensity >= 45:
        state = "FEAR"
    elif bias_score >= 25:
        state = "BULLISH"
    elif bias_score <= -25:
        state = "BEARISH"
    else:
        state = "NEUTRAL"

    confidence = int(round((available / total_possible) * 100))

    if not reasons:
        reasons.append("Belirgin bir sinyal yok, piyasa dengeli gorunuyor.")

    return {
        "state": state,
        "bias_score": bias_score,
        "intensity": intensity,
        "confidence": confidence,
        "reasons": reasons,
        "inputs": inputs,
        "signal_weights_applied": {k: _w(weights, k) for k in ("rsi", "fear_greed", "funding", "oi_price", "price", "news", "social")},
        "bullish_points": round(bullish_points, 1),
        "bearish_points": round(bearish_points, 1),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json
    result = evaluate(
        rsi=38,
        fear_greed_value=42,
        funding_rate=-0.00025,
        oi_change_pct=-4.2,
        price_change_pct=1.8,
        news_sentiment=-22,
        social_sentiment=-18,
        signal_weights={"social": 0.5, "news": 1.2},
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
