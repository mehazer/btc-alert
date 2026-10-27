"""
agents/market_agent.py
------------------------
Deterministik, kural-tabanli Market Agent (v2 - Directional).

AMAC:
Zaten toplanmis sinyalleri (RSI, Fear & Greed, Funding Rate,
OI degisimi, Fiyat degisimi, News Sentiment) tek bir
yonlu (directional) piyasa degerlendirmesine cevirir.

Cikti:
- state: BULLISH | BEARISH | NEUTRAL | OVERHEATED | FEAR
- bias_score: -100 (cok bearish) ... +100 (cok bullish)
- intensity: 0-100 (sinyalin guclu olup olmadigi)
- confidence: 0-100 (veri tamligi)
- reasons: okunabilir gerekceler

Tasarim ilkeleri:
- Tamamen deterministik, kural tabanli (LLM yok)
- Yeni veri kaynagi eklemez, sadece parametre alir
- Bagimsiz modul
"""

from datetime import datetime, timezone


def _clamp(value, low, high):
    return max(low, min(high, value))


def evaluate(
    rsi=None,
    fear_greed_value=None,
    funding_rate=None,
    oi_change_pct=None,
    price_change_pct=None,
    news_sentiment=None,
):
    """
    Directional market evaluation.

    Parameters (all optional):
        rsi: float 0-100
        fear_greed_value: int 0-100
        funding_rate: float (e.g. 0.0001 = +0.01%)
        oi_change_pct: float (percentage change)
        price_change_pct: float (percentage change)
        news_sentiment: float -100 to +100

    Returns dict with state, bias_score, intensity, confidence, reasons...
    """
    bullish_points = 0.0
    bearish_points = 0.0
    reasons = []
    available = 0
    total_possible = 6

    inputs = {
        "rsi": rsi,
        "fear_greed_value": fear_greed_value,
        "funding_rate": funding_rate,
        "oi_change_pct": oi_change_pct,
        "price_change_pct": price_change_pct,
        "news_sentiment": news_sentiment,
    }

    # ------------------------------------------------------------------
    # 1. RSI
    # ------------------------------------------------------------------
    if rsi is not None:
        available += 1
        if rsi >= 75:
            bullish_points += 25
            reasons.append(f"RSI yuksek ({rsi:.1f}) -> asiri alim baskisi")
        elif rsi >= 60:
            bullish_points += 12
            reasons.append(f"RSI guclu ({rsi:.1f}) -> boğa egilimi")
        elif rsi <= 25:
            bearish_points += 25
            reasons.append(f"RSI cok dusuk ({rsi:.1f}) -> asiri satim / korku")
        elif rsi <= 40:
            bearish_points += 12
            reasons.append(f"RSI zayif ({rsi:.1f}) -> ayi egilimi")

    # ------------------------------------------------------------------
    # 2. Fear & Greed
    # ------------------------------------------------------------------
    if fear_greed_value is not None:
        available += 1
        if fear_greed_value >= 80:
            bullish_points += 20
            reasons.append(f"Fear&Greed asiri acgozlu ({fear_greed_value})")
        elif fear_greed_value >= 60:
            bullish_points += 10
            reasons.append(f"Fear&Greed iyimser ({fear_greed_value})")
        elif fear_greed_value <= 20:
            bearish_points += 20
            reasons.append(f"Fear&Greed asiri korku ({fear_greed_value})")
        elif fear_greed_value <= 40:
            bearish_points += 10
            reasons.append(f"Fear&Greed korku bolgesi ({fear_greed_value})")

    # ------------------------------------------------------------------
    # 3. Funding Rate (very important directional signal)
    # ------------------------------------------------------------------
    if funding_rate is not None:
        available += 1
        # Convert to percentage for readability (0.0001 -> 0.01%)
        funding_pct = funding_rate * 100
        if funding_rate >= 0.0003:  # >= 0.03%
            bullish_points += 22
            reasons.append(f"Funding Rate yuksek (+{funding_pct:.4f}%) -> long'lar kalabalik")
        elif funding_rate >= 0.0001:
            bullish_points += 10
            reasons.append(f"Funding Rate pozitif (+{funding_pct:.4f}%)")
        elif funding_rate <= -0.0003:
            bearish_points += 22
            reasons.append(f"Funding Rate cok negatif ({funding_pct:.4f}%) -> short'lar kalabalik")
        elif funding_rate <= -0.0001:
            bearish_points += 10
            reasons.append(f"Funding Rate negatif ({funding_pct:.4f}%)")

    # ------------------------------------------------------------------
    # 4. OI + Price Divergence (classic)
    # ------------------------------------------------------------------
    if oi_change_pct is not None and price_change_pct is not None:
        available += 1
        if price_change_pct > 1.5 and oi_change_pct > 3:
            bullish_points += 18
            reasons.append("Fiyat + OI birlikte yukseldi -> guclu boğa trendi")
        elif price_change_pct > 1.5 and oi_change_pct < -2:
            # Weak rally / short covering
            bearish_points += 8
            reasons.append("Fiyat yukseldi ama OI dustu -> zayif ralli / short covering")
        elif price_change_pct < -1.5 and oi_change_pct > 3:
            bearish_points += 18
            reasons.append("Fiyat dustu + OI artti -> guclu ayi baskisi")
        elif price_change_pct < -1.5 and oi_change_pct < -2:
            bullish_points += 8
            reasons.append("Fiyat dustu + OI dustu -> long temizligi (potansiyel dibe yaklasma)")
    elif oi_change_pct is not None:
        available += 0.5
        if oi_change_pct > 8:
            reasons.append(f"OI belirgin artti (+{oi_change_pct:.1f}%)")
        elif oi_change_pct < -8:
            reasons.append(f"OI belirgin azaldi ({oi_change_pct:.1f}%)")

    # ------------------------------------------------------------------
    # 5. Pure price momentum (smaller weight)
    # ------------------------------------------------------------------
    if price_change_pct is not None:
        available += 0.5
        if price_change_pct > 4:
            bullish_points += 8
            reasons.append(f"Guclu fiyat yukselişi (+{price_change_pct:.1f}%)")
        elif price_change_pct < -4:
            bearish_points += 8
            reasons.append(f"Guclu fiyat dususu ({price_change_pct:.1f}%)")

    # ------------------------------------------------------------------
    # 6. News Sentiment
    # ------------------------------------------------------------------
    if news_sentiment is not None:
        available += 1
        if news_sentiment >= 40:
            bullish_points += 15
            reasons.append(f"Haber sentimenti iyimser ({news_sentiment:+.0f})")
        elif news_sentiment >= 15:
            bullish_points += 7
            reasons.append(f"Haber sentimenti hafif pozitif ({news_sentiment:+.0f})")
        elif news_sentiment <= -40:
            bearish_points += 15
            reasons.append(f"Haber sentimenti kotumser ({news_sentiment:+.0f})")
        elif news_sentiment <= -15:
            bearish_points += 7
            reasons.append(f"Haber sentimenti hafif negatif ({news_sentiment:+.0f})")

    # ------------------------------------------------------------------
    # Final scores
    # ------------------------------------------------------------------
    net = bullish_points - bearish_points
    bias_score = int(_clamp(net * 1.4, -100, 100))  # scale a bit

    intensity = int(_clamp(max(bullish_points, bearish_points), 0, 100))

    # State decision
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
        "bullish_points": round(bullish_points, 1),
        "bearish_points": round(bearish_points, 1),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json
    # Quick self-test
    result = evaluate(
        rsi=38,
        fear_greed_value=42,
        funding_rate=-0.00025,
        oi_change_pct=-4.2,
        price_change_pct=1.8,
        news_sentiment=-22,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
