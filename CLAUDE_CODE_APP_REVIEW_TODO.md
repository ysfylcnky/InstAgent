# InstaAgent — Meta App Review Öncesi Yapılacaklar (Claude Code görev promptu)

> Bu dosya Claude Code'a verilecek promptdur. Amaç: App Review'a göndermeden önce
> (1) SaaS roadmap'te yarım kalan adımları tamamlamak, (2) Meta App Review'ın kod
> tarafında zorunlu kıldığı uçları eklemek. Manuel (Meta paneli) adımlar en sonda
> Yusuf'un checklist'i olarak listelidir; onları Claude Code yapmaz, sadece
> destekleyici dosyaları üretir.

## 0. Kurallar (TOKEN İSRAFINI ÖNLE)

- **Şu dosyaları AÇMA** (dev repo dump'ları, gereksiz): `latest.md` (~1MB), `repomix.md` (~500KB), `.venv/`, `__pycache__/`, `.pytest_cache/`, `model_cost_compare.py`, `gpt4o_test_senaryolari.md`.
- **Bağlam için yeterli özet:** `INSTAAGENT_SAAS_IMPLEMENTATION.md` (§5 secrets, §11 OAuth, §16 teknik borç) ve `INSTAAGENT_SAAS_ROADMAP.md` (Faz 3, 9, 10). Bunlar zaten tüm mimariyi anlatıyor; başka özet aramaya gerek yok.
- Her görev için **yalnızca aşağıda adı geçen dosyaları oku.** Kör `grep`/tam dosya taraması yapma; verilen fonksiyon/satır işaretlerini kullan.
- Testler: `./.venv/Scripts/python.exe -m pytest tests/` (SQLite + fakeredis, 2 tenant fixture'ı `tests/conftest.py`). Her yeni uç için test ekle; mevcut 51 test kırılmamalı.
- Sır sızdırma yok: token/secret log/response/exception'a düşmeyecek (mevcut redaction desenini koru).
- Değişiklikler additive ve rollback-safe olsun.

---

## BÖLÜM A — Meta App Review zorunlu kod uçları (BLOKER, roadmap'te yok)

Bunlar App Review onayı için gerekli; şu an kodda **yok** (grep ile doğrulandı).

### A1. Webhook imza doğrulaması (X-Hub-Signature-256)
- **Dosya:** `main.py` — POST `/webhook` handler (`instagram_webhook`, `_process_instagram_webhook` çağırır, ~s.1350). GET doğrulaması `verify_webhook` (~s.1279) zaten var, ona dokunma.
- **Yap:** POST body'sini işlemeden önce `X-Hub-Signature-256` başlığını `META_APP_SECRET` (`config.py` s.36) ile HMAC-SHA256 hesaplayıp sabit-zamanlı (`hmac.compare_digest`) doğrula. Uyuşmazsa 403 dön, işleme. Ham body'yi (raw bytes) imza için oku — parse edilmiş JSON değil.
- **Test:** `tests/test_webhook_signature.py` — geçerli imza kabul, geçersiz/eksik imza 403.

### A2. Veri Silme Callback'i (Data Deletion Request)
- **Dosya:** `main.py` yeni endpoint (örn. `POST /data-deletion`), silme mantığı `Services/`'te yeni bir yardımcı (örn. `Services/gdpr_service.py`).
- **Yap:** Meta `signed_request` gövdesini `META_APP_SECRET` ile doğrula/çöz (base64url + HMAC-SHA256). İçinden gelen `user_id` (IGSID) için ilgili tenant kapsamında müşteri verisini sil: `customers`, `conversations`, `orders`, `usage_logs` (IGSID = `sender`/`customer_phone`/`phone`). Silme için `Services/db.py` session'ı kullan (tenant çözümü gerekiyorsa `scoped=False` + ilgili tenant). Meta'nın beklediği JSON'u dön: `{"url": "<durum takip url>", "confirmation_code": "<kod>"}`.
- **Test:** `tests/test_data_deletion.py` — geçerli signed_request ile ilgili IGSID kayıtları siliniyor, başka tenant/başka kullanıcı verisi etkilenmiyor, geçersiz imza reddediliyor.

### A3. Deauthorize Callback'i
- **Dosya:** `main.py` yeni endpoint (örn. `POST /deauthorize`).
- **Yap:** Kullanıcı uygulamayı kaldırınca Meta buraya `signed_request` POST eder. İmzayı doğrula; ilgili tenant'ın IG bağlantısını pasifleştir (token'ı temizle / `tenants` kaydını `status=inactive` yap — mevcut `tenants.status` alanını kullan, bkz. IMPLEMENTATION §11/§16). Sır loglama.
- **Test:** `tests/test_deauthorize.py` — imza doğrulama + bağlantının pasifleşmesi.

### A4. Privacy Policy + Terms of Service sayfaları (public URL)
- **Dosya:** `main.py` iki GET route (`/privacy`, `/terms`) + `templates/privacy.html`, `templates/terms.html` (mevcut dashboard.css'i kullan, sade).
- **İçerik:** InstaAgent'ın Instagram mesajlaşma verisini nasıl işlediği, sakladığı (Fernet şifreli secret, tenant izolasyonu), sakladığı süre, silme talebi yolu (A2 endpoint'i). Meta App Review privacy policy URL alanına `/privacy` verilecek.
- Not: App Review privacy policy URL'sini **public erişilebilir** ister; route auth'suz olmalı.

---

## BÖLÜM B — SaaS roadmap'te yarım kalan adımlar (IMPLEMENTATION §16)

Bunlar App Review'ı bloke etmez ama "çok-tenant SaaS" iddiasını tamamlar. Öncelik: B1 > B2 > B3 > B4.

### B1. Kurulum sihirbazı tenant_settings'e yazsın (Faz 3'ün eksiği)
- **Sorun:** `Services/setup_service.py` s.123-124 IG/İKAS/OpenAI creds'ini `"target": "env"` ile hâlâ `.env`'e yazıyor. SaaS'ta her tenant kendi ayarını DB'ye yazmalı.
- **Dosya:** `Services/setup_service.py` (target env → tenant settings), `Services/settings_service.py` (set/get, `SECRET_SETTING_KEYS` zaten şifreliyor — bkz. IMPLEMENTATION §5).
- **Yap:** Tenant-owned anahtarları (`IG_ACCOUNT_ID`, `IG_ACCESS_TOKEN`, `IG_API_BASE`, `IKAS_*`, `OPENAI_API_KEY`, `MODEL_NAME`, `STORE_*`, `WHATSAPP_*`) aktif tenant kapsamında `settings_service` ile yaz. `IG_API_BASE`'i de yazılabilir alan yap (setup ekranında alan yok — ekle; graph.facebook.com / graph.instagram.com seçimi, bkz. aşağıda). Sistem sırları (`META_APP_*`, `JWT_SECRET`, `ENCRYPTION_KEY`, MySQL/Redis, `VERIFY_TOKEN`) env'de kalmaya devam.
- **UI:** `static/js/setup.js` (s.21-22 civarı IG alanları) + `templates/setup.html` — `IG_API_BASE` için "Bağlantı türü" alanı ekle (Instagram Login = graph.instagram.com / Facebook Sayfası = graph.facebook.com). Access Token ipucundaki "System User önerilir" metnini düzelt: Instagram Login'de token dashboard'daki "Generate token"dan alınır, ~60 gün geçerlidir.
- **Test:** `tests/test_settings_secrets.py`'yi genişlet — setup ile yazılan creds DB'de (şifreli) ve doğru tenant'ta.

### B2. Per-tenant setup gating
- **Sorun:** `_setup_gate` middleware ve `setup_service._setup_complete_cache` süreç-global (IMPLEMENTATION §16). Bir tenant kurulumu bitirince global "kurulum tamam" sayılıyor.
- **Dosya:** `main.py` (`_setup_gate`), `Services/setup_service.py` (cache).
- **Yap:** Kurulum tamamlanma durumunu aktif tenant'a göre hesapla (tenant'ın zorunlu ayarları dolu mu). Cache tenant-namespaced olsun. Yalnız kurulum yönlendirmesini etkiler; veriyi değil.
- **Test:** `tests/test_onboarding.py`'ye ekle — tenant A kurulumu tamam, tenant B hâlâ setup'a yönlendiriliyor.

### B3. Kredensiyel değişiminde cache invalidation
- **Sorun:** OpenAI/İKAS client cache ve tenant resolver cache, key/creds değişince eskime bırakabilir (IMPLEMENTATION §16).
- **Dosya:** `Services/openai_service.py`, `Services/ikas_service.py`, `Services/tenant_service.py` (`invalidate()`), çağrı yeri `Services/setup_service.py` + OAuth kaydı (`Services/meta_oauth_service.py`).
- **Yap:** Setup/OAuth ile creds yazıldığında ilgili `invalidate_*` çağrılsın (tenant-scoped). `ig_account_id` değişirse tenant resolver cache'i temizle.
- **Test:** cache invalidation birim testi (mevcut `tests/test_session_isolation.py` desenini izle).

### B4. Faz 10 `harden` migration'ını gerçek MySQL'de çalıştır/duman testi
- **Sorun:** `migrations/run.py harden` (tenant_id NOT NULL + `customers/settings` bileşik PK + `tenants.ig_account_id UNIQUE`) prod MySQL'de henüz doğrulanmadı (IMPLEMENTATION §14, §16). Entrypoint yalnız `apply` çalıştırıyor.
- **Yap:** Bu bir kod değil, **çalıştırma/doğrulama** görevi. `migrations/run.py`'yi oku, `harden` yolunu gerçek MySQL'e karşı (staging) çalıştır, NULL kalan satır varsa fail-safe'i doğrula. Sorun çıkarsa rapor et; kod değişikliği gerekiyorsa öner.
- Not: Faz 12 (billing) kapsam dışı — DOKUNMA.

---

## BÖLÜM C — Landing (ana sayfa) — WhatsAgent tasarımının InstaAgent uyarlaması

App Review'dan bağımsız, paralel yapılabilir. Kardeş proje **WhatsAgent**'ın ana sayfası
referans alınacak, **Instagram**'a uyarlanacak.

- **Dosya:** `templates/landing.html` **zaten var** (~31KB) + `/` route'u `main.py`'de. Sıfırdan yazma; mevcut dosyayı OKU ve aşağıdaki referansa göre yeniden tasarla. Stil için `static/css/dashboard.css` değişkenlerini (`--violet`, `--green`, `--surface`, `--muted`, aurora sınıfları) kullan; setup.html'deki `aurora` desenini örnek al.
- **Marka farkı:** WhatsApp yeşili yerine **Instagram gradyanı** (mor→pembe→turuncu) aksan rengi; logo Instagram ikonu; isim **InstaAgent**; kanal "WhatsApp" değil **Instagram DM**.

**Referans düzen (WhatsAgent ana sayfası — görselden birebir çıkarıldı):**
- Koyu (neredeyse siyah) arka plan + köşelerde yumuşak gradyan "aurora" ışımaları.
- **Üst nav:** solda yuvarlak köşeli kare içinde ikon + "InstaAgent / Yapay Zeka Satış Asistanı" alt başlığı; ortada linkler: Özellikler · Nasıl Çalışır · Fiyatlar · SSS; sağda "Giriş Yap" (outline) + "Ücretsiz Dene" (dolu, gradyan) butonları.
- **Hero sol:** üstte rozet "⚡ Instagram'da 7/24 otomatik satış"; büyük başlık "Müşterileriniz yazsın, yapay zeka satışı kapatsın." (2. ve 3. satır gradyan renkli); altında açıklama paragrafı (InstaAgent, mağazana Instagram DM'den gelen her mesaja saniyeler içinde yanıt verir; ürünü bulur, renk/beden seçer, siparişi adına alır); iki CTA: "14 Gün Ücretsiz Deneyin →" (dolu) + "Nasıl Çalışır?" (outline); altında güven satırı "✓ Kredi kartı gerekmez · Kurulum dakikalar sürer".
- **Hero sağ:** sohbet mockup kartı — üstte "InstaAgent · çevrimiçi" başlığı; örnek Instagram DM diyaloğu (müşteri: "Merhaba, siyah spor ayakkabınız var mı?" → bot ürünü öneriyor, numara soruyor → müşteri "42 numara olsun" → bot sepete ekliyor, ad-soyad/adres istiyor → müşteri bilgi veriyor → bot "Siparişiniz alındı ✅"); köşede yüzen rozet "~8 sn ortalama yanıt".
- **Ek bölümler** (nav linklerine karşılık, aşağı kaydırınca): Özellikler (kartlar), Nasıl Çalışır (3-4 adım), Fiyatlar (plan kartları), SSS (accordion). Mevcut landing.html'de bunlar varsa koru/uyarla.
- **Responsive:** mobilde hero tek kolona düşsün, nav hamburger olsun.

**Doğrulama:** Tarayıcıda `/` aç, masaüstü + mobil genişlikte görünümü kontrol et (kırık düzen/taşma yok). Metinler Instagram'a göre; hiçbir yerde "WhatsApp" kalmamış. `dashboard.css` dışında yeni ağır bağımlılık ekleme.

---

## Test & doğrulama (bitişte zorunlu)
1. `./.venv/Scripts/python.exe -m pytest tests/` — tüm eski + yeni testler geçmeli.
2. Yeni endpoint'leri elle de dene: imza doğrulama (A1/A2/A3) geçersiz imzada 403.
3. Sızıntı kontrolü: yeni kodda token/secret loglanmıyor.
4. Kısa bir `APP_REVIEW_CHANGES.md` üret: eklenen uçlar, dosyalar, testler, privacy/terms URL'leri.

---

## Yusuf'un manuel checklist'i (Meta paneli — Claude Code yapmaz)
Claude Code bunları yapamaz; sadece destek dosyalarını (privacy/terms içeriği A4, reviewer test adımları) üretir. Aşağıyı `REVIEWER_TEST_INSTRUCTIONS.md` olarak Claude Code hazırlasın, Yusuf panele girsin.

1. **App'i Live moda al** + Business Verification'ı tamamla.
2. **İzin talebi:** `instagram_business_basic` + `instagram_business_manage_messages` için gerekçe + kullanım açıklaması gir.
3. **Test hesabı:** App Dashboard → Roles → **Instagram Testers**'a bir test Instagram (professional) hesabı ekle; hesap davetini kabul etsin. Reviewer bu hesapla ya da senin verdiğin adımlarla test eder.
4. **Screencast:** Müşteri DM atıyor → bot yanıtlıyor akışını kaydet (izinlerin nasıl kullanıldığını gösteren zorunlu video).
5. **Privacy Policy URL** = `https://<domain>/privacy`, **Data Deletion Callback URL** = `https://<domain>/data-deletion`, **Deauthorize Callback URL** = `https://<domain>/deauthorize` alanlarını App ayarlarına gir (A2/A3/A4'ten sonra).
6. **Webhook:** Instagram webhook'unun `messages` alanına abone olduğundan ve `VERIFY_TOKEN`'ın eşleştiğinden emin ol.
7. Reviewer test adımlarını (`REVIEWER_TEST_INSTRUCTIONS.md`) submission'a ekle ve gönder.
