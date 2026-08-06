# Mumio — App Review Hazırlık Değişiklikleri

Bu belge, Meta App Review öncesi eklenen kod uçlarını, dokunulan dosyaları,
testleri ve panele girilecek URL'leri özetler. Tüm değişiklikler **additive** ve
**rollback-safe**'tir; mevcut davranış korunur.

Test durumu: **75 test geçiyor** (önceki 55 + 20 yeni). Çalıştırma:
```bash
./.venv/Scripts/python.exe -m pytest tests/
```

---

## BÖLÜM A — Meta App Review zorunlu uçları

### A1 · Webhook imza doğrulaması (`X-Hub-Signature-256`)
- **Ne:** POST `/webhook` gövdeyi işlemeden önce ham gövde üzerinden
  `HMAC-SHA256` hesaplar ve `X-Hub-Signature-256` başlığıyla **sabit-zamanlı**
  (`hmac.compare_digest`) karşılaştırır. Uyuşmazsa/eksikse **403**, işlemez.
- **Davranış:** `META_APP_SECRET` tanımlıysa imza **ZORUNLU**. Tanımlı değilse
  (yalnız yerel/test) doğrulama atlanır ve uyarı loglanır — **üretimde
  `META_APP_SECRET` mutlaka tanımlanmalıdır.**
- **Dosyalar:** `Services/meta_verify.py` (yeni), `main.py` (`instagram_webhook`).
- **Test:** `tests/test_webhook_signature.py` (geçerli/geçersiz/eksik/kurcalanmış).

### A2 · Veri Silme Callback'i (Data Deletion Request)
- **Ne:** POST `/data-deletion`. Meta `signed_request`'i `META_APP_SECRET` ile
  doğrular/çözer; içinden gelen `user_id` (IGSID) için müşteri verisini
  **tüm tenant'lardan** siler: `conversations`, `orders`, `customers`,
  `usage_logs`. Meta'nın beklediği JSON'u döner:
  `{"url": "<durum takip url>", "confirmation_code": "<kod>"}`.
- **Durum sayfası:** GET `/data-deletion/status?code=...` (public).
- **Dosyalar:** `Services/gdpr_service.py` (yeni), `Services/meta_verify.py`,
  `main.py`, `templates/deletion_status.html` (yeni).
- **Test:** `tests/test_data_deletion.py` (silme + izolasyon + geçersiz imza).

### A3 · Deauthorize Callback'i
- **Ne:** POST `/deauthorize`. `signed_request` doğrulanır; ilgili tenant'ın
  Instagram bağlantısı pasifleştirilir: `tenants.status = "inactive"` +
  `IG_ACCESS_TOKEN` temizlenir + resolver cache invalidate edilir. Eşleşen
  tenant yoksa fail-safe `{"ok": true}`.
- **Not:** `signed_request.user_id`, bağlı IG hesap kimliği (tenant anahtarı =
  IG Business Account ID) olarak yorumlanır. Meta payload'u farklı bir kimlik
  döndürürse eşleşme bulunmaz ve hiçbir tenant etkilenmez.
- **Dosyalar:** `Services/gdpr_service.py`, `main.py`.
- **Test:** `tests/test_deauthorize.py` (pasifleşme + geçersiz imza + fail-safe).

### A4 · Gizlilik Politikası + Kullanım Koşulları (public)
- **Ne:** GET `/privacy` ve GET `/terms` — auth'suz, public.
- **İçerik:** IG mesajlaşma verisinin nasıl işlendiği/saklandığı (Fernet şifreli
  sırlar, tenant izolasyonu), saklama süresi, silme yolu (A2 ucu), üçüncü taraf
  hizmetler (Meta/OpenAI/İKAS).
- **Dosyalar:** `main.py`, `templates/privacy.html` + `templates/terms.html` (yeni).
- **Landing footer'a** `/privacy` ve `/terms` bağlantıları eklendi.

> **İletişim adresi:** `info@mumifashion.com` (privacy.html ve terms.html'de girildi).

---

## BÖLÜM B — SaaS roadmap tamamlama

### B1 · Kurulum sihirbazı tenant_settings'e yazar (SİSTEM/MÜŞTERİ ayrımı)
- **MÜŞTERİ (tenant) credential'ları** artık `.env` yerine **tenant settings
  (DB)**'e yazılır ve kurulum ekranından girilir: `IG_ACCOUNT_ID`,
  `IG_ACCESS_TOKEN`, `IG_API_BASE`, `IKAS_*`, `WHATSAPP_*`, `STORE_*`. Secret'lar
  Fernet ile şifreli.
- **SİSTEM (platform) anahtarları** kurulum ekranında SORULMAZ; operatörün
  `.env`'inden okunur: `OPENAI_API_KEY`, `MODEL_NAME` (OpenAI maliyeti merkezî),
  `VERIFY_TOKEN`, `META_APP_*`, `JWT_SECRET`, `ENCRYPTION_KEY`, MySQL/Redis.
  Kurulum sihirbazında ayrı bir "Yapay Zeka" bölümü YOKTUR; `config.openai_api_key()`
  ve `config.model_name()` doğrudan `.env`'den okur.
- Kurulum tamamlanması yalnız MÜŞTERİ ayarlarına bakar (sistem anahtarları
  müşteri kurulumunu gate'lemez).
- **IG_API_BASE** artık yazılabilir alan: setup ekranında "Bağlantı Türü"
  açılır listesi (Instagram Login = `graph.instagram.com` / Facebook Sayfası =
  `graph.facebook.com`). Access Token ipucu güncellendi (Instagram Login'de
  ~60 gün geçerli "Generate token").
- **Routing senkronu:** Setup'ta girilen `IG_ACCOUNT_ID`, webhook routing
  anahtarı olan `tenants.ig_account_id` sütununa da yazılır (çakışma reddedilir).
- **Dosyalar:** `Services/setup_service.py`, `static/js/setup.js`,
  `templates/setup.html`.
- **Test:** `tests/test_settings_secrets.py` (creds DB'de şifreli + doğru tenant
  + çapraz hesap reddi).

### B2 · Per-tenant kurulum gating
- Kurulum tamamlanma durumu artık **aktif tenant'a göre** hesaplanır; tamamlanma
  mandalı tenant-namespaced. Middleware (`_setup_gate`) JWT'den tenant çözüp o
  tenant kapsamında kontrol eder. Bir tenant'ın kurulumu diğerini "tamam" yapmaz.
- **Dosyalar:** `Services/setup_service.py` (`is_setup_complete`, `mark_complete`,
  `reset_setup_cache`), `main.py` (`_setup_gate`).
- **Test:** `tests/test_onboarding.py` (per-tenant tamamlanma + eksik alan/flag).

### B3 · Kredensiyel değişiminde cache invalidation
- Setup/OAuth ile creds yazılınca ilgili tenant-scoped cache'ler tazelenir:
  OpenAI client (`openai_service.invalidate_client`), İKAS token/ürün
  (`ikas_service.invalidate` — yeni), hesap→tenant resolver
  (`tenant_service.invalidate`), kurulum mandalı (`reset_setup_cache`).
- **Dosyalar:** `Services/ikas_service.py` (yeni `invalidate`),
  `Services/setup_service.py`, `Services/meta_oauth_service.py`.
- **Test:** `tests/test_cache_invalidation.py`.

### B4 · `harden` migration incelemesi (rapor)
- Bu ortamda gerçek staging MySQL yok (mysql connector kurulu değil); `harden`
  yalnız MySQL DDL'i olduğundan **çalıştırma Yusuf'a bırakılır** (aşağıdaki
  checklist). İnceleme bulguları:
  - `harden()` **fail-safe**: herhangi bir tabloda `tenant_id IS NULL` varsa
    hardening'i durdurur (önce `apply`/backfill gerekir).
  - Bileşik PK dönüşümü (`customers`→(tenant_id,phone), `settings`→(tenant_id,skey))
    backfill sonrası çakışmasızdır; `customers.phone`'a FK referansı yoktur.
  - **Kapatılan boşluk:** `tenants.ig_account_id UNIQUE` artık `harden` içinde de
    **idempotent** garantilenir (var olan kısıt tekrar eklenmez). Önceden yalnız
    model'deki `unique=True` ile (create_all) kuruluyordu.
- **Dosya:** `migrations/run.py` (`_ensure_unique_ig_account`, `harden`).

**Staging harden checklist (Yusuf):**
1. DB yedeği al.
2. `python -m migrations.run apply` çalıştır.
3. İzolasyon testlerini doğrula, hiçbir satırda `tenant_id` NULL kalmadığını kontrol et.
4. `python -m migrations.run harden` çalıştır. NULL kalırsa fail-safe durdurur.

---

## BÖLÜM C — Landing (ana sayfa)
- Mevcut Instagram temalı landing korundu ve cilalandı (WhatsAgent düzeninin
  Instagram uyarlaması: mor→pembe→turuncu gradyan, IG DM mockup, ~7 sn rozet).
- **Mobil hamburger menü** eklendi: nav linkleri + CTA açılır menüde (önceden
  hamburger doğrudan talep formunu açıyordu).
- Footer'a `/privacy` ve `/terms` bağlantıları; telif yılı 2026.
- **Doğrulama:** masaüstü (1280px) ve mobil (375px) genişlikte yatay taşma yok,
  hero mobilde tek kolon, "WhatsApp" metni hiçbir yerde yok.
- **Dosya:** `templates/landing.html`. **Test:** `tests/test_landing.py` (mevcut).

---

## Meta App paneline girilecek URL'ler
Mumio domain'i: **`ig.mumifashion.com`** (`api.mumifashion.com` WhatsAgent'a aittir).

| Alan | URL |
|---|---|
| Privacy Policy URL | `https://ig.mumifashion.com/privacy` |
| Terms of Service URL | `https://ig.mumifashion.com/terms` |
| Data Deletion Callback URL | `https://ig.mumifashion.com/data-deletion` |
| Deauthorize Callback URL | `https://ig.mumifashion.com/deauthorize` |
| Webhook Callback URL | `https://ig.mumifashion.com/webhook` (`messages` alanına abone) |
| Landing (tanıtım) | `https://ig.mumifashion.com/` (veya `/instagent`) |

## Zorunlu ortam değişkenleri (üretim)
- `META_APP_SECRET` — **A1/A2/A3 imza doğrulaması bunsuz çalışmaz.**
- `VERIFY_TOKEN` — GET `/webhook` doğrulaması; Meta paneline birebir aynısı girilir.
- `ENCRYPTION_KEY` — tenant sırlarının Fernet şifrelemesi.

## Güvenlik notu
Yeni kodda token/secret log/response/exception'a düşmez: `meta_verify.py` ve
`gdpr_service.py` hiçbir şey yazdırmaz; callback logları yalnız maskeli kimlik
(son 4 hane) ve durum içerir.
