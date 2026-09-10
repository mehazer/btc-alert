# PROJECT_SUMMARY.md

## Not (onemli, seffaflik icin)

Bu dokuman, projenin GitHub repo'suna dogrudan erisimim olmadigi icin,
bu sohbette birlikte adim adim insa ettigimiz ve senin dogruladigin
son calisan koda dayanarak yazildi. Eger repo'da bu dokumanda
anlatilandan farkli bir sey varsa (ornegin son bir degisiklik commit
edilmemisse), bana guncel `alert_bot.py` ve `docs/index.html`
icerigini gonder, bu dokumani ona gore duzeltirim.

Reddit veri toplama ozelligi bu analize DAHIL EDILMEDI, cunku (a) sen
"mevcut durum" listende onu belirtmedin, (b) bu gorevin talimatlari
acikca "yeni veri kaynagi ekleme" diyor. Eger Reddit ozelligini de
uygulamis/uygulayacaksan, bu dokuman guncellenmeli.

---

## 1. Proje Ozeti

**btc-alert**, Binance/Bybit/alternative.me/RSS gibi herkese acik
(API key gerektirmeyen) kaynaklardan kripto piyasa verisi toplayan,
bunu Telegram'a bildirim olarak gonderen ve statik bir web
dashboard'unda (GitHub Pages) gosteren, tamamen ucretsiz altyapi
uzerinde (GitHub Actions) calisan bir sistemdir.

Mimari felsefe: sunucusuz (serverless), key/secret sadece GitHub
Secrets icinde tutulur, hicbir bilesen kalici bir sunucu gerektirmez.

## 2. Modul ve Dosya Envanteri

### 2.1 `alert_bot.py` (ana orkestratör / tek script)

Tum is mantigi bu tek dosyada. Fonksiyon bazinda:

| Fonksiyon | Gorev | Veri Kaynagi | Basarisizlikta davranis |
|---|---|---|---|
| `fetch_klines()` | Haftalik kapanis fiyati + hacim | Binance Spot (`data-api.binance.vision`) | Exception firlatir, cagiran yer bunu Telegram'a hata mesaji olarak gonderir, sembol islenmez |
| `fetch_open_interest()` | Anlik Open Interest | Bybit Futures public API | Exception firlatir, `open_interest = None` olarak devam edilir (kritik degil) |
| `fetch_fear_greed_index()` | Piyasa geneli F&G skoru (0-100) | alternative.me | Exception firlatir, `None` olarak devam edilir |
| `fetch_news_headlines()` | Guncel haber basliklari | CoinDesk + CoinTelegraph RSS | Kaynak bazinda try/except, biri basarisiz olursa digeri calismaya devam eder |
| `compute_rsi()` | Wilder RSI hesaplama (harici kutuphane yok) | `fetch_klines()` ciktisi | Yetersiz veri varsa `ValueError` |
| `load_history()` / `save_history()` | Sembol bazli tarihce okuma/yazma | `history/{SYMBOL}.json` | Bozuk JSON ise sifirdan baslar |
| `save_market_snapshot()` | Piyasa geneli (sembole ozel olmayan) son durumu kaydeder | `history/market.json` | - |
| `send_telegram_message()` | Telegram bildirimi | Telegram Bot API | Basarisizlik `print` ile loglanir, script cokmez |
| `process_symbol()` | Bir sembol icin tum akisi yurutur (veri cek, RSI hesapla, tarihceye yaz, esik kontrolu, mesaj gonder) | Yukaridaki fonksiyonlar | Kritik hata (kline/RSI) → `False` doner, `main()` bunu `sys.exit(1)`'e cevirir (Actions'ta kirmizi X) |
| `main()` | Calisma sirasini yonetir: once piyasa geneli veri (F&G+haber), sonra her sembol | - | - |

**Onemli davranissal detay:** `ALWAYS_NOTIFY` ortam degiskeni
(varsayilan `false`), esik asilmasa bile test amacli her calismada
mesaj gonderilmesini saglar. Production'da `false` kalmali.

### 2.2 `.github/workflows/rsi_alert.yml`

GitHub Actions workflow. Gunde bir kez (cron) veya manuel
(`workflow_dispatch`) tetiklenir. Adimlar: repo checkout → Python
kurulumu → bagimlilik kurulumu (`pip install -r requirements.txt`) →
`python alert_bot.py` calistirma → (ayri bir adim) `history/`
klasorunu commit+push ile repo'ya geri yazma.

**Bilinen risk:** Ayni anda birden fazla calistirma (manuel + otomatik
cakismasi) `history/` push'unda "rejected, fetch first" hatasina
sebep oldu (yasandi, cozuldu ama kalici onlem — `concurrency` bloku —
workflow dosyasina eklenmis olabilir/olmayabilir, kontrol edilmeli).

### 2.3 `requirements.txt`

Tek bagimlilik: `requests`. Kasitli olarak minimal tutuldu (harici
TA/RSI kutuphanesi kullanilmiyor) — GitHub Actions'ta derleme/kurulum
sorunu cikarma riskini azaltmak icin.

### 2.4 `docs/index.html` (dashboard, GitHub Pages)

Tamamen istemci tarafi (client-side) statik sayfa. Backend yok.
Tarayicida calisan JavaScript, `raw.githubusercontent.com` uzerinden
dogrudan `history/*.json` dosyalarini `fetch()` ile okuyup ekrana
basar. Su an gosterilenler: Fear & Greed rozeti, haber basliklari,
sembol basina son RSI/fiyat + son 5 kaydin tablosu.

**Onemli mimari nokta:** Dashboard, hicbir secret'a (Telegram token
vb.) erismiyor — sadece zaten public olan JSON verisini okuyor.
Guvenlik acisindan bu temiz bir ayrim.

### 2.5 `history/` klasoru (veri deposu)

Veritabani yerine kullanilan, git ile versiyonlanan JSON dosyalari:
- `history/BTCUSDT.json`, `ETHUSDT.json`, `SOLUSDT.json`: sembol
  basina zaman serisi (tarih, rsi, fiyat, hacim, oi, f&g) — son 200
  kayitla sinirli (`MAX_HISTORY_ENTRIES`)
- `history/market.json`: piyasa geneli son durum (tek kayit,
  uzerine yazilir, tarihce tutmaz)

### 2.6 GitHub Secrets (kod icinde YOK, sadece ortam degiskeni olarak okunuyor)

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Iyi pratik: kodun hicbir
yerinde bu degerler sabit (hardcoded) yazilmiyor.

## 3. Veri Akisi (Data Flow)

```
[GitHub Actions cron/manuel tetikleme]
              |
              v
   alert_bot.py calisir (Python 3.11, GitHub-hosted runner)
              |
   +----------+----------------------------+
   |          |              |             |
   v          v              v             v
Binance    Bybit OI     alternative.me   RSS (CoinDesk/
Spot API   (sik 403)    Fear&Greed      CoinTelegraph)
   |          |              |             |
   +----------+----+---------+-------------+
                    v
        process_symbol() her sembol icin:
        RSI hesapla -> esik kontrolu -> Telegram'a gonder (kosullu)
                    |
                    v
        history/{SYMBOL}.json + history/market.json'a yazilir
                    |
                    v
        Workflow: "git commit + push" ile bu JSON'lar repo'ya geri yazilir
                    |
                    v
   [Kullanici tarayicisi] docs/index.html'i acar
                    |
                    v
   Sayfadaki JS, raw.githubusercontent.com'dan JSON'lari CEKER
   (fetch calisma zamaninda, sayfa acildiginda olur — build-time degil)
                    |
                    v
        Tarayicida tablo/kart olarak render edilir
```

**Kritik nokta:** Dashboard'un veri tazeligi, GitHub Actions'in ne
siklikta calistigina ve raw.githubusercontent.com'un CDN cache
davranisina bagli — "gercek zamanli" degil, "gunluk/periyodik
anlik goruntu" (snapshot) mantigidir.

## 4. Zayif Noktalar ve Olcekleme Riskleri

Bunlari onem sirasina gore, dogrudan/olcekleme riski ayrimiyla
listeliyorum:

### 4.1 Dis veri kaynaklarina asiri bagimlilik (YUKSEK risk)
Binance Futures (451), sonra Bybit (403) — ikisi de GitHub Actions'in
paylasimli bulut IP'lerini engelledi. Ayni kader RSS/haber
kaynaklarinin (ozellikle Cloudflare korumali olanlarin) da basina
gelebilir. **Bu proje, hicbir SLA'si olmayan, ucretsiz, herkese acik
API'lere zincirleme bagimli** — bu, "trading intelligence platform"
olma iddiasi buyudukce en buyuk kirilganlik noktasi olacak.

### 4.2 Veritabani yerine git-tracked JSON dosyalari (ORTA-YUKSEK risk, buyudukce artar)
- Sorgulama yok (belirli bir tarih araligi, filtreleme, agregasyon
  icin SQL/indeks yok)
- Repo boyutu zamanla surekli buyur (git, her JSON degisikligini
  tarihiyle tutar) — yillar surdukce repo klonlama/CI suresi yavaslar
- Es zamanli yazma guvenligi yok — iki workflow calismasi ayni anda
  push etmeye calisirsa cakisma olur (bu zaten bir kez yasandi)
- Sema (schema) versiyonlanmiyor: bir alan adi degisirse/silinirse,
  dashboard JS'i sessizce bozulabilir (hata firlatmadan yanlis/eksik
  gosterebilir)

### 4.3 Sessiz basarisizlik riski (ORTA risk)
Workflow basarisiz olursa (`exit(1)`), bunun tek gorunur yeri GitHub
Actions sekmesindeki kirmizi X'tir. Kullaniciya (sana) proaktif bir
"bot calismadi" bildirimi gitmiyor — bunu fark etmek icin Actions
sekmesine bakman gerekiyor. Bir sistem "trading intelligence"
iddiasindaysa, kendi basarisizligini bildirmesi kritik hale gelir.

### 4.4 Retry/backoff mekanizmasi yok (DUSUK-ORTA risk)
Her API cagrisi tek seferlik denenir. Genelde gecici (transient) agi
hatalarinda bile ikinci sans verilmiyor — bu, gercekte hicbir sorun
olmadigi halde "veri alinamadi" sonucuna yol acabiliyor.

### 4.5 Tek monolitik script (OLCEKLEME riski, bugun sorun degil)
Tum mantik `alert_bot.py` icinde. Bugun icin sorun yaratmiyor (kod
hala fonksiyon bazinda modular), ama coklu-ajan (multi-agent) mimarisi
buyudukce, veri toplama / analiz / bildirim / sunum katmanlarinin
fiziksel olarak (ayri dosya/modul) ayrilmasi gerekecek — bu talebin
7. adimi (klasor yapisi) tam olarak bunu hedefliyor.

### 4.6 Dashboard'un GitHub raw content rate limit'ine bagimliligi (DUSUK risk, simdilik)
`raw.githubusercontent.com`, kimliksiz (unauthenticated) istekler
icin IP basina saatlik istek siniri uygular. Su an dusuk trafikle
(sadece sen) sorun degil; sayfa baskalarina acilirsa veya sik sik
yenilenirse ileride sorun cikarabilir.

### 4.7 Testin/CI dogrulamasinin olmamasi (DUSUK-ORTA risk)
Kod degisiklikleri dogrudan `main`'e commit ediliyor, hicbir otomatik
test/dogrulama (syntax kontrolu disinda) calismiyor. Bu sohbette
birkac kez syntax hatasi/eksik fonksiyon gibi hatalar production'a
(yani calisan workflow'a) kadar gitti ve orada fark edildi.

## 5. Bu Analizden Cikan Oncelik Sirasi (ileride ele alinmasi icin, simdi DEGIL)

Sadece bilgi amacli, aksiyon gerektirmiyor:
1. Workflow'a `concurrency` blogu (cakisan run'lari onlemek icin) —
   eklenmis mi kontrol edilmeli
2. Basarisizlik durumunda Telegram'a "bot calismadi" bildirimi
3. API cagrilarina basit retry (2-3 deneme, kisa bekleme)
4. Orta vadede: JSON dosyalari yerine hafif bir veritabani (SQLite
   dahi git-tracked JSON'dan daha sorgulanabilir olur) — ama bu
   gorevin talimati acikca "PostgreSQL'i simdi ekleme" dedigi icin
   bu asamada YAPILMIYOR.

---

*Bu dokuman, mevcut sistemi degistirmeden sadece analiz eder. Kod
degisikligi bu adimdan SONRA, ayri adimlarda yapilacak.*
