# InstagramAgent

WhatsApp satış asistanının **Instagram DM** için bağımsız kopyası. Yapay zeka,
ikas ürün arama, sipariş akışı, oturum yönetimi ve yönetim paneli WhatsApp
projesiyle **birebir aynıdır**; yalnızca müşteri kanalı Instagram Messaging
API'sine uyarlanmıştır.

Bu proje WhatsApp projesinden **tamamen izoledir**: kendi `.env`'i, kendi
MySQL veritabanı, kendi Redis anahtar alanı (`ig:session:`) ve kendi portu
(8001) vardır. İkisi aynı sunucuda yan yana çalışabilir.

## WhatsApp projesine göre farklar

| Katman | Durum |
|---|---|
| LLM (`openai_service`), ikas (`ikas_service`), sipariş (`order_service`), oturum (`session_store`), panel, promptlar | Aynen kopyalandı |
| Müşteriye gönderim | `Services/instagram_service.py` (IG Messaging API) |
| Medya indirme | `Services/media_service.py` — IG'de ek dosya URL olarak gelir |
| Webhook parse | `main.py` — IG `entry[].messaging[]`, `sender.id` (IGSID), attachments, referral |
| Mağaza bildirimi | Yine WhatsApp'tan (`STORE_NOTIFY_PHONE`) — opsiyonel |
| Redis prefix | `ig:session:` (WhatsApp: `wa:session:`) |
| DB | Ayrı veritabanı (`MYSQL_DATABASE=instaagent`) |

## Ön koşullar (Meta tarafı)

- **Instagram Professional** (Business/Creator) hesabı.
- Meta App + **`instagram_business_manage_messages`** izni (App Review).
- "Instagram API with Instagram Login" ya da bağlı Facebook Sayfası.
- Webhook aboneliği: `messages` alanı; callback URL = `https://<alanadi>/webhook`,
  Verify Token = `.env`'deki `VERIFY_TOKEN`.

## Önemli kısıt — 24 saat penceresi

Instagram'da bota **yalnızca kullanıcının son mesajından itibaren 24 saat içinde**
serbest metin cevabı gönderebilirsin. Bot gelen mesaja anında cevap verdiği için
normal akışta sorun olmaz. Ancak 24 saat sessizlikten sonra **proaktif** mesaj
(ör. "dekont hatırlatması") API tarafından reddedilir — WhatsApp'taki template
mesajının Instagram'da dengi yoktur.

## Kurulum (yerel)

```bash
cp .env.example .env          # değerleri doldur
python generate_password_hash.py   # panel parola hash'i üret → DASHBOARD_PASSWORD_HASH
# JWT_SECRET üret:
python -c "import secrets; print(secrets.token_urlsafe(48))"

pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8001
```

MySQL ve Redis çalışıyor olmalı. İlk açılışta panel `/dashboard` seni Kurulum
sihirbazına (`/dashboard/settings/setup`) yönlendirir; Instagram, OpenAI ve ikas
bilgilerini girip test edip **Kurulumu Tamamla**'ya bas.

## Kurulum (Docker)

```bash
cp .env.example .env          # değerleri doldur (MYSQL_HOST otomatik "mysql" olur)
docker compose up -d --build
```

Uygulama `127.0.0.1:8001`'de dinler; production'da nginx (TLS) ile
`ig.<alanadi>` üzerinden yayınlanır.

## Notlar

- Panel arayüzünde bazı yerlerde hâlâ "WhatsAgent" ibaresi görünebilir (kozmetik);
  istersen `templates/` ve `static/js/` içinde toplu değiştirilebilir.
- Mağaza bildirimi için WhatsApp bilgilerini (Kurulum → Bildirimler) girmek
  opsiyoneldir; boş bırakılırsa sipariş yalnız panele/DB'ye kaydedilir.
