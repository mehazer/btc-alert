# ARCHITECTURE.md

Bu dokuman, projenin bugunku ("Market Agent" eklendikten sonraki)
klasor yapisini ve coklu-ajan (multi-agent) mimarisine dogru
planlanan genislemeyi anlatir.

## Onerilen Klasor Yapisi (bugunden itibaren gecerli)

```
btc-alert/
├── alert_bot.py                    # Orkestratör: veri toplama +
│                                    #   RSI/tarihce/Telegram/Market
│                                    #   Agent cagirma
├── agents/
│   ├── __init__.py
│   ├── market_agent.py             # UYGULANDI - deterministik
│   │                                #   piyasa durumu degerlendirmesi
│   ├── social_agent.py             # PLACEHOLDER - henuz yok
│   ├── news_agent.py               # PLACEHOLDER - henuz yok
│   ├── whale_agent.py              # PLACEHOLDER - henuz yok
│   └── decision_agent.py           # PLACEHOLDER - henuz yok
├── docs/
│   └── index.html                  # Dashboard (GitHub Pages)
├── history/                        # Veri deposu (JSON, git-tracked)
│   ├── BTCUSDT.json
│   ├── ETHUSDT.json
│   ├── SOLUSDT.json
│   ├── market.json                 # Genel piyasa ozeti (F&G+haber)
│   └── market_agent_history.json   # YENI - Market Agent kayitlari
├── .github/workflows/
│   └── rsi_alert.yml
├── requirements.txt
├── README.md
├── PROJECT_SUMMARY.md              # YENI - mevcut durum analizi
└── ARCHITECTURE.md                 # YENI - bu dosya
```

## Neden Ajanlar `agents/` Altinda, Ayri Dosyalarda?

Tek bir buyuk script yerine her ajani ayri dosyada tutmanin sebebi
bugun degil, YARIN icin: dort ayri ajan (Market/Social/News/Whale) +
bunlari birlestiren bir Decision Agent eklendiginde, hepsi tek
dosyada olsaydi hem test etmek hem de "hangi ajan neden su karari
verdi" diye ayiklamak zorlasirdi.

**Bagimsizlik kurali (kritik):** Her ajan dosyasi SADECE kendi
mantigini icerir, digerlerini import ETMEZ. Ajanlar arasi iletisim
SADECE ayni sozlesmeye (dict: `state`, `risk_score`, `confidence`,
`reasons`) uyan girdi/cikti uzerinden olur. Bu sayede:
- `whale_agent.py` gelistirilirken `market_agent.py`'ye dokunma
  ihtimali sifira iner
- Herhangi bir ajan tek basina test edilebilir (`python
  agents/market_agent.py` gibi — zaten `market_agent.py` boyle
  calisiyor)
- `decision_agent.py`, diger ajanlarin IC mantigindan tamamen
  habersiz kalir, sadece disariya verdikleri sonucu okur

## Ortak Sozlesme (Contract)

Her ajanin `evaluate()` fonksiyonu (Decision Agent haric — o
`decide()` fonksiyonuyla digerlerinin CIKTISINI alir) su sekilde bir
sozluk dondurmelidir:

```python
{
    "state": str,           # ajana ozel durum etiketleri
    "risk_score": int,      # 0-100
    "confidence": int,      # 0-100
    "reasons": list[str],   # okunabilir gerekceler
    "inputs": dict,         # kullanilan ham girdiler (loglama icin)
    "evaluated_at": str,    # ISO8601 UTC zaman damgasi
}
```

Bu sozlesmeye uyuldugu surece, ileride `decision_agent.py` tum
ajanlarin ciktilarini ayni sekilde isleyebilir.

## Gelecek Ajanlar (SADECE plan + placeholder, henuz uygulanmadi)

| Ajan | Amac | Bilinen zorluk |
|---|---|---|
| `social_agent.py` | Sosyal medya (Reddit/X) gundeminden ilgi/coskunluk skoru | Veri kaynagi secimi (Reddit public JSON kullanilabilir, X ucretli) |
| `news_agent.py` | Haber basliklarindan duyarlilik (sentiment) skoru | Basit anahtar-kelime yaklasimi ile baslanmali, AI kullanilmamali (bu asamada) |
| `whale_agent.py` | Buyuk cuzdan/borsa giris-cikis hareketleri | Ucretsiz, guvenilir bir veri kaynagi bulmak digerlerinden daha zor |
| `decision_agent.py` | Tum ajanlarin ciktisini TEK bir nihai karara birlestirmek | Ajanlar arasi agirliklandirma stratejisi ayrica tasarlanmali |

**Onemli:** Bu dort ajan SU ANDA SADECE dosya iskeleti + docstring
olarak mevcut (`NotImplementedError` firlatirlar). Hicbiri
`alert_bot.py` tarafindan cagrilmiyor. Bunlarin gercek
implementasyonu, bu dokumanda tanimlanan mimariyi bozmadan, ayri
gorevler olarak ele alinmalidir.

## Genisleme Sirasinda Degismemesi Gereken Ilkeler

1. Her ajan bagimsiz kalmali (yukaridaki kural)
2. Yeni bir ajan eklerken `alert_bot.py`'nin mevcut is akisi
   (RSI/Telegram/dashboard) BOZULMAMALI — yeni ajan `main()`
   icinde ayri, izole bir cagri olarak eklenmeli (bkz.
   `run_market_agent()` fonksiyonunun `alert_bot.py` icindeki
   ornek kullanimi)
3. Basarisiz bir ajan, digerlerini veya ana RSI/Telegram akisini
   COKERTMEMELI (try/except ile izole edilmeli — `market_agent.py`
   entegrasyonunda bu zaten uygulandi: `primary_symbol_data is None`
   kontrolu Market Agent'i guvenli sekilde atlar)
4. Yeni veri deposu ihtiyaci dogarsa (ornegin `social_agent.py` icin
   ayri bir tarihce), ayni JSON-dosyasi deseni izlenmeli — PostgreSQL
   gibi bir veritabani, bu proje kapsaminda HENUZ eklenmeyecek
