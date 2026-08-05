# InstaAgent — Meta App Review Yol Haritası

**Durum özeti (5 Ağustos 2026):** Kod tarafı büyük ölçüde hazır. Zorunlu uçların
hepsi mevcut ve canlıda: `/privacy`, `/terms`, `/webhook` (GET fail-closed
doğrulandı), `/data-deletion`, `/deauthorize`, `/data-deletion/status`.
Kalan iş **8 madde** — 3'ü bloker.

| # | İş | Kim | Blokerlik |
|---|---|---|---|
| K1 | Webhook imza doğrulaması prod'da gerçekten geçiyor mu? | Yusuf + kod | 🔴 BLOKER |
| K2 | `main.py` içindeki geçici TEŞHİS bloğunu sil | Kod | 🔴 BLOKER |
| K3 | Reviewer için demo panel hesabı + test akışı | Kod + Yusuf | 🔴 BLOKER |
| K4 | Instagram bağlan butonu — marka uyumu + İngilizce | Kod | 🟠 Yüksek risk |
| K5 | App ayarları: ikon 1024×1024, kategori, business e-posta | Yusuf (panel) | 🟠 Yüksek risk |
| K6 | Instagram Testers'a hesap ekle | Yusuf (panel) | 🟠 Yüksek risk |
| K7 | Screencast (İngilizce altyazılı) | Yusuf | 🟠 Yüksek risk |
| K8 | Submission metinleri (izin gerekçeleri + reviewer talimatı) | Kod (hazırlanacak) | 🟡 Gerekli |

> **Business Verification** hâlâ Meta'da incelemede. Advanced Access onayı bunsuz
> verilmez, ama submission'ı paralel hazırlayabiliriz — verification bitince
> gönderiyoruz.

---

## 🔴 K1 — Webhook imzası prod'da geçiyor mu?

**Neden bloker:** `main.py:1521` — `META_APP_SECRET` tanımlıysa imza **zorunlu**;
uyuşmazsa 403 dönüp gövde işlenmiyor. `main.py:1495-1520` arasında hâlâ duran
"TEŞHİS 2" bloğu, imza uyuşmazlığı yaşandığını ve debug edildiğini gösteriyor.

Eğer prod'da imza şu an **başarısız**sa: reviewer test DM'i attığında bot cevap
vermez → Meta "app inaccessible / permission not demonstrated" diyerek **tüm
submission'ı reddeder.** Bu Meta'nın 1 numaralı red sebebi.

**Doğrulama:**

1. Kendi IG Business hesabına DM at.
2. Sunucu loglarına bak: `docker compose logs -f --tail=100`
3. `[TESHIS2] EslesTI=True` görüyorsan imza geçiyor → K2'ye geç.
   `EslesTI=False` görüyorsan imza kırık → önce bunu çözeriz.

**`False` çıkarsa muhtemel sebepler** (`docs/meta-integration.md` §Signature):

- Ters proxy (nginx/Caddy/Cloudflare) gövdeyi yeniden serialize ediyor →
  Meta'nın `\/` kaçışlı JSON'u bozuluyor. Log'daki `kacisli_slash=0` ise sebep bu.
- `.env`'deki `META_APP_SECRET` panel'deki **App Secret** ile aynı değil
  (Instagram Login kullanan app'te *Instagram App Secret* ile *Meta App Secret*
  farklı olabilir — webhook'u hangisi imzalıyorsa o kullanılmalı).

---

## 🔴 K2 — Geçici teşhis bloğunu sil

**Dosya:** `main.py:1495-1520`, ayrıca `Services/ikas_service.py:613-630`.

Şu an her webhook isteğinde:

- Ham gövde `/app/last_webhook_body.bin` dosyasına yazılıyor → **müşteri mesaj
  içeriği diske düşüyor.** Gizlilik politikamızda yazan taahhütle çelişir.
- HMAC hex'leri ve `secret_sha8` loglanıyor.

Meta'nın red sebeplerinden biri: *"You are still developing your app —
submission will be rejected."* Debug çıktısı bu izlenimi verir.

**Yapılacak:** Blokları kaldır, sunucudaki `/app/last_webhook_body.bin` dosyasını
sil, testleri çalıştır (`pytest tests/`).

---

## 🔴 K3 — Reviewer'ın uygulamayı test edebilmesi

Meta'nın App Verification adımı aynen şunu istiyor:

> *"Provide detailed step-by-step instructions for Meta reviewers to **log in and
> test your app**... provide any required test credentials."*

**Mevcut durum:** `/login` var ama **public kayıt yok** — landing'deki
"Ücretsiz Dene" formu (`POST /kayit`) sadece lead kaydediyor, hesap açmıyor.
Reviewer kendi başına giremez.

**Yapılacak:**

1. Reviewer için ayrı bir **demo tenant + panel kullanıcısı** aç
   (ör. `metareview` / güçlü şifre). Kendi canlı tenant'ından ayrı olsun ki
   reviewer gerçek müşteri verisi görmesin.
2. Bu tenant'a kendi test IG Business hesabını bağla **veya** reviewer'ın kendi
   hesabını bağlayabilmesi için Kurulum ekranını erişilebilir bırak.
3. Kimlik bilgilerini submission'ın "Credentials" alanına gir.

> ⚠️ Sahte Facebook/Instagram hesabı **kullanma** — Meta bunu tespit ederse tüm
> submission reddedilir. Kendi gerçek hesabın uygun.

---

## 🟠 K4 — Instagram bağlan butonu

Meta'nın action item'ı: *"Verify that the login button or link is visible in your
app and screencast, and adheres to our brand guidelines."*

**Mevcut:** `static/js/setup.js:136` → `Instagram'ı Bağla` (Türkçe, özel stil).

**Risk:** Buton Meta'nın "Business Login for Instagram" marka kurallarına
uymuyor olabilir + reviewer Türkçe metni tanımayabilir.

**Yapılacak:** Butonu Meta'nın önerdiği biçime yaklaştır — Instagram glyph +
`Connect with Instagram` (veya `Instagram'ı Bağla (Connect with Instagram)`
çift dilli). Kurulum ekranına reviewer için İngilizce ipucu ekle.

---

## 🟠 K5 — App ayarları (Meta paneli)

App Review → **Review your app settings** altında dördü de dolu olmalı:

| Alan | Değer |
|---|---|
| App icon | 1024×1024 PNG — InstaAgent logosu |
| Privacy Policy URL | `https://ig.mumifashion.com/privacy` |
| App Category | *Business & Pages* (veya *Messaging*) |
| Business Email | `info@mumifashion.com` |

Ayrıca **Instagram → API setup with Instagram login** altında:

| Alan | Değer |
|---|---|
| Webhook Callback URL | `https://ig.mumifashion.com/webhook` — `messages` alanı abone |
| Deauthorize Callback URL | `https://ig.mumifashion.com/deauthorize` |
| Data Deletion Callback URL | `https://ig.mumifashion.com/data-deletion` |
| OAuth Redirect URI | `https://ig.mumifashion.com/connect/instagram/callback` |

> **Not:** `/data-deletion` ve `/deauthorize` tarayıcıda açılmaz — bunlar
> **sadece POST** kabul eden makine uçları. Tarayıcıdan girince "Method Not
> Allowed" görmen **normaldir ve doğrudur.** Meta bunları imzalı `signed_request`
> gövdesiyle POST eder. Test etmek için aşağıdaki komutu kullan.

---

## 🟠 K6 — Instagram Testers ekleme (adım adım)

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) → InstaAgent app'ini aç.
2. Sol menü → **App roles** → **Roles**.
3. **Instagram Testers** bölümü → **Add people** (veya **Add Instagram Testers**).
4. Test edilecek Instagram **professional/business** hesabının kullanıcı adını yaz → **Add**.
5. O hesapla Instagram'a gir → **Ayarlar → Hesap → Web sitesi izinleri →
   Tester davetleri** (mobil: *Settings and privacy → Apps and websites → Tester
   invites*) → daveti **kabul et**.
6. Panelde davetin `Accepted` göründüğünü doğrula.

Bunu hem kendi IG Business hesabın hem de test için kullandığın bireysel hesabın
için yap.

---

## 🟠 K7 — Screencast

Meta kuralları: **İngilizce UI tercih edilir**, değilse **altyazı/tooltip
zorunlu**. Her izin için ayrı ayrı gösterilmeli.

**Çekilecek akış (tek video yeter, iki izni de kapsar):**

1. `ig.mumifashion.com/login` → demo hesapla giriş *(altyazı: "Merchant logs into InstaAgent")*
2. Kurulum → **Connect with Instagram** butonuna tıkla *(altyazı: "Business Login for Instagram — requests instagram_business_basic + instagram_business_manage_messages")*
3. Instagram yetkilendirme ekranı → izinleri onayla
4. Panelde bağlı hesabın kullanıcı adı görünür *(altyazı: "instagram_business_basic — app reads account ID and username to identify the merchant")*
5. Telefondan/ikinci hesaptan bağlı hesaba DM at: *"Bu ürün stokta var mı?"*
6. Bot saniyeler içinde yanıtlar *(altyazı: "instagram_business_manage_messages — receives the DM via webhook and sends the automated reply")*
7. Ürün adı → renk/beden → sipariş onayı akışını tamamla
8. Panelde **Konuşmalar** sekmesinde aynı mesajların göründüğünü göster

Ekran kaydında Türkçe kalan her buton için altyazı/İngilizce tooltip ekle.

---

## 🟡 K8 — Submission metinleri

Hazırlanacak (İngilizce, kopyala-yapıştır):

- `instagram_business_basic` gerekçesi
- `instagram_business_manage_messages` gerekçesi
- Platform Settings → step-by-step reviewer talimatı
- Credentials alanı içeriği

Mevcut `REVIEWER_TEST_INSTRUCTIONS.md` iyi bir taslak; demo hesap bilgileri ve
Meta'nın istediği alan-alan formata göre güncellenecek.

---

## Ek: uçları kendin test etme komutları

Sunucuda (veya app secret'ın olduğu bir yerde) çalıştır:

```bash
# /data-deletion ve /deauthorize'ı gerçek imzalı istekle test et
APP_SECRET='<META_APP_SECRET>'
USER_ID='<test IGSID veya IG account id>'
python3 - <<'PY'
import base64, hmac, hashlib, json, os, urllib.request, urllib.parse
secret = os.environ['APP_SECRET'].encode()
payload = {"user_id": os.environ['USER_ID'], "algorithm": "HMAC-SHA256"}
raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=')
sig = base64.urlsafe_b64encode(hmac.new(secret, raw, hashlib.sha256).digest()).rstrip(b'=')
sr = sig.decode() + '.' + raw.decode()
for path in ('/data-deletion', '/deauthorize'):
    req = urllib.request.Request(
        'https://ig.mumifashion.com' + path,
        data=urllib.parse.urlencode({'signed_request': sr}).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    print(path, urllib.request.urlopen(req).read().decode())
PY
```

Beklenen: `/data-deletion` → `{"url": "...", "confirmation_code": "..."}`,
`/deauthorize` → `{"ok": true}`. Geçersiz imzada ikisi de **403**.

> ⚠️ `/deauthorize` testi, `user_id` gerçek bir `tenants.ig_account_id` ile
> eşleşirse o tenant'ı **gerçekten pasifleştirir.** Test ederken var olmayan bir
> ID kullan.
