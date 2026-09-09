# BTC Weekly RSI Alert Bot

Binance'ten BTC/USDT haftalık RSI'yi çeker, eşik (varsayılan 80) aşılırsa
Telegram'a bildirim atar. GitHub Actions ile tamamen ücretsiz, sunucusuz
7/24 çalışır.

## 1. Telegram bot oluştur (5 dakika)

1. Telegram'da **@BotFather**'ı bul, `/newbot` yaz.
2. Bot için bir isim ve kullanıcı adı ver (kullanıcı adı `bot` ile bitmeli).
3. BotFather sana bir **token** verecek — bunu not al
   (örnek: `123456789:ABCdefGhIJKlmNoPQRstuVwxyZ`).
4. Şimdi kendi botunla bir sohbet başlat (Telegram'da botunu bul, `/start` yaz).
5. Chat ID'ni öğrenmek için tarayıcıda şu adresi aç (TOKEN'ı kendi tokenınla değiştir):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Dönen JSON içinde `"chat":{"id": ...}` kısmındaki sayı senin **chat_id**'in.

## 2. GitHub'da repo oluştur

1. GitHub'da yeni bir **public** repo aç (public olması Actions'ı tamamen
   ücretsiz ve sınırsız yapıyor; private de olur ama aylık 2000 dakika
   sınırı var — bu bot günde birkaç saniye çalıştığı için sınıra
   yaklaşman neredeyse imkansız).
2. Bu klasördeki dosyaları (`alert_bot.py`, `requirements.txt`,
   `.github/workflows/rsi_alert.yml`, bu `README.md`) repo'ya yükle.

## 3. Secrets'ı ekle

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

- `TELEGRAM_BOT_TOKEN` → BotFather'dan aldığın token
- `TELEGRAM_CHAT_ID` → yukarıda bulduğun chat id

## 4. Test et

Repo → **Actions** sekmesi → "BTC Weekly RSI Alert" workflow'u seç →
**Run workflow** ile manuel tetikle. Loglarda RSI değerini göreceksin.
RSI eşiği aşmıyorsa Telegram'a mesaj gelmez (bu normal) — logda
"Esik asilmadi" yazar.

RSI'yi zorla test etmek istersen `RSI_THRESHOLD` değerini workflow
dosyasında geçici olarak `1` yapıp tekrar çalıştırabilirsin, mesaj
geleceğini göreceksin.

## Sonra ne yapılabilir (opsiyonel genişletmeler)

- ETH ve SOL için ayrı job'lar eklemek (aynı script'i `SYMBOL` değiştirerek
  tekrar kullan)
- Günlük RSI + haftalık RSI'yi birlikte takip etmek
- Fiyat eşiği bazlı ayrı bir uyarı eklemek
- Bildirimleri bir veritabanına (ör. basit bir GitHub Gist ya da
  Google Sheet) loglamak, böylece GHC backtest'ine veri kaynağı olur

Bunların hiçbiri şu an gerekli değil — önce bunun 3-4 gün sorunsuz
çalıştığını gör, sonra üstüne ekle.
