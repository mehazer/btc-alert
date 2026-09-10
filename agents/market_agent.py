------------------------
Deterministik, kural-tabanli Market Agent.

AMAC: Zaten toplanmis sinyalleri (RSI, Fear & Greed Index, Open
Interest degisimi, BTC fiyat degisimi) tek bir genel piyasa durumu
degerlendirmesine ceviren: bir Risk Skoru, bir Guven (Confidence)
Skoru, bir Durum (State) etiketi ve okunabilir Gerekceler (Reasons)
listesi ureten saf bir fonksiyon.

TASARIM ILKELERI (proje gereksinimlerine gore):
- Tamamen deterministik, kural tabanli. Burada YAPAY ZEKA/LLM
  muhakemesi YOKTUR.
- Yeni veri kaynagi eklemez. Sadece baska yerde zaten toplanmis
  degerleri parametre olarak alir.
- Bagimsiz modul: kendi basina hicbir veri cekmez, Telegram/dashboard/
  """GitHub'dan haberi yoktur. Girdi alir, sozluk (dict) doner. Bu, test
  etmeyi kolaylastirir ve ileride diger ajanlarin (Social/News/Whale/
  Decision) bu modulden habersiz, bagimsiz gelistirilebilmesini saglar.

ACIKCA BELIRTILMESI GEREKEN VARSAYIMLAR
(orijinal istekte bu detaylar acik birakilmisti, ben asagidaki
kararlari aldim ve gerekcelerini yaziyorum):

1. Skor kademeleri KUMULATIF DEGIL. Her girdi, sadece ulastigi EN
   YUKSEK kademenin puanini katkida bulunur (ornegin RSI=85 icin
   +30 alinir, +20+30=+50 DEGIL). Gerekce: cakisan kademeleri ust
   uste toplamak ayni sinyali iki kez saymak olurdu ve kademe
   sinirlarinda skoru istikrarsizlastirirdi.

2. Risk Skoru 100'de sinirlandirilir (cap), her ne kadar dort
   girdinin maksimum degerleri toplami 110 olsa da (RSI 30 + F&G 30
   + OI 30 + Fiyat 20), cunku istek acikca "Risk Score (0-100)"
   diyor.

3. Confidence (Guven) Skoru, istatistiksel bir model guveni DEGIL,
   VERI TAMLIGINI yansitir: dort girdiden kacinin gercekten mevcut
   oldugunun (None olmayan) yuzdesidir. Bu secimin sebebi: Open
   Interest'in projenin gecmisinde sik sik alinamadigi biliniyor
   (bkz. PROJECT_SUMMARY.md) — eksik veriyle hesaplanan bir Risk
   Skorunun, tam veriyle hesaplanana gore daha az agirlik tasidigini
   GORUNUR kilmak istedik.

4. Nihai (capped) Risk Skoru uzerinden Durum (State) esikleri:
     0-29   -> NORMAL
     30-59  -> BULLISH
     60-84  -> OVERHEATED
     85-100 -> EXTREME_EUPHORIA
   Bunlar ILK VE AYARLANABILIR bir gecis noktasidir — herhangi bir
   backtest/geriye donuk test ile turetilmedi. Ayarlamak icin
   asagidaki STATE_THRESHOLDS listesini degistirmen yeterli, skor
   mantigina dokunmana gerek yok.

5. OI_CHANGE_PCT ve PRICE_CHANGE_PCT girdileri MUTLAK DEGER (abs())
   olarak degerlendirilir — yani hem sert bir yukselis hem sert bir
   dusus ayni sekilde "asiri hareket" olarak puanlanir (yon degil,
   buyukluk onemli). Bu, "overheated/euphoria" kavraminin volatilite
   temelli oldugu varsayimina dayanir.
"""

from datetime import datetime, timezone

# ---------------------------------------------------------------------
# Ayarlanabilir esikler (mantiktan ayri tutuldu, ileride degistirmek
# skor hesaplama kodunu degistirmeyi gerektirmesin diye).
# Her tier: (esik_degeri, puan). Yuksekten dusuge SIRALI olmali.
# ---------------------------------------------------------------------

RSI_TIERS = [(80, 30), (70, 20)]
FEAR_GREED_TIERS = [(85, 30), (75, 20)]
OI_CHANGE_PCT_TIERS = [(20, 30), (10, 20)]      # yuzde, mutlak deger
PRICE_CHANGE_PCT_TIERS = [(20, 20), (10, 10)]   # yuzde, mutlak deger

STATE_THRESHOLDS = [
    (85, "EXTREME_EUPHORIA"),
    (60, "OVERHEATED"),
    (30, "BULLISH"),
    (0, "NORMAL"),
]


def _tier_score(value, tiers, label, unit=""):
    """`value`'nun ulastigi en yuksek kademenin (puan, gerekce_metni)
    ikilisini dondurur. `tiers` yuksek esikten dusuge sirali olmali.
    `value` None ise (veri mevcut degilse) (0, None) doner."""
    if value is None:
        return 0, None
    for threshold, score in tiers:
        if value >= threshold:
            return score, f"{label} >= {threshold}{unit} (+{score})"
    return 0, None


def _resolve_state(score):
    for threshold, state in STATE_THRESHOLDS:
        if score >= threshold:
            return state
    return "NORMAL"


def evaluate(rsi=None, fear_greed_value=None, oi_change_pct=None, price_change_pct=None):
    """
    Mevcut piyasa durumunu, zaten toplanmis sinyallerden degerlendirir.

    Parametreler (hepsi opsiyonel — mevcut degilse None gecin):
        rsi: float, haftalik RSI degeri (0-100)
        fear_greed_value: int, Fear & Greed Index (0-100)
        oi_change_pct: float, Open Interest yuzde degisimi
            (ornegin +12.5 icin 12.5). Fonksiyon icinde mutlak
            deger alinir, siz pozitif/negatif gonderebilirsiniz.
        price_change_pct: float, BTC fiyat yuzde degisimi (ayni
            sekilde mutlak deger alinir).

    Donen sozluk:
        {
            "state": str,
            "risk_score": int (0-100),
            "confidence": int (0-100),
            "reasons": list[str],
            "inputs": {...ham girdiler, loglama/tarihce icin...},
            "evaluated_at": ISO8601 UTC zaman damgasi,
        }
    """
    raw_score = 0
    reasons = []

    inputs = {
        "rsi": rsi,
        "fear_greed_value": fear_greed_value,
        "oi_change_pct": oi_change_pct,
        "price_change_pct": price_change_pct,
    }

    score, reason = _tier_score(rsi, RSI_TIERS, "RSI")
    raw_score += score
    if reason:
        reasons.append(reason)

    score, reason = _tier_score(fear_greed_value, FEAR_GREED_TIERS, "Fear & Greed")
    raw_score += score
    if reason:
        reasons.append(reason)

    abs_oi_change = abs(oi_change_pct) if oi_change_pct is not None else None
    score, reason = _tier_score(abs_oi_change, OI_CHANGE_PCT_TIERS, "OI degisimi", "%")
    raw_score += score
    if reason:
        reasons.append(reason)

    abs_price_change = abs(price_change_pct) if price_change_pct is not None else None
    score, reason = _tier_score(abs_price_change, PRICE_CHANGE_PCT_TIERS, "Fiyat degisimi", "%")
    raw_score += score
    if reason:
        reasons.append(reason)

    risk_score = min(raw_score, 100)

    available_inputs = sum(1 for v in inputs.values() if v is not None)
    confidence = round((available_inputs / len(inputs)) * 100)

    if not reasons:
        reasons.append("Belirgin bir esik asilmadi, piyasa normal araliginda.")

    state = _resolve_state(risk_score)

    return {
        "state": state,
        "risk_score": risk_score,
        "confidence": confidence,
        "reasons": reasons,
        "inputs": inputs,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    # Basit, disariya hicbir istek atmayan manuel test.
    import json
    result = evaluate(rsi=82, fear_greed_value=88, oi_change_pct=15, price_change_pct=12)
    print(json.dumps(result, indent=2, ensure_ascii=False))
