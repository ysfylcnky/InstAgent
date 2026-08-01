# InstaAgent — Multi-Tenant SaaS Dönüşüm Yol Haritası

> Durum: **Planlama tamamlandı, implementasyon başlıyor.**
> Bu belge InstaAgent'ın tek-müşterili (single-tenant) yapıdan production-safe,
> çok-kiracılı (multi-tenant) bir SaaS mimarisine kademeli dönüşümünü tanımlar.

---

## 0. Analiz Özeti (kod okunarak çıkarıldı)

### 0.1 Mevcut mimari
- **Framework:** FastAPI (`main.py`), Jinja2 SSR panel, statik JS/CSS.
- **DB:** MySQL 8. İki katman bir arada:
  - **Ham SQL** (`Services/usage_logger.get_connection()` — `mysql-connector` havuzu):
    `usage_logger.log_usage`, `order_service.save_order`, `settings_service`, `setup_service._db_ok`.
  - **SQLAlchemy ORM** (`Services/db.py` + `Services/models.py`):
    `conversation_logger.log_message` (yazma), `dashboard_service` (tüm okuma sorguları).
  - Yani Faz 0 (ORM zemini) **kısmen** yapılmış; okuma tarafı ORM, bazı yazmalar hâlâ ham SQL.
- **Redis:** `Services/session_store.py` — sohbet oturumları (`ig:session:{igsid}`), TTL'li, stateless app.
- **Auth:** Tek kullanıcı. `DASHBOARD_USER` + bcrypt hash (`.env`), JWT httpOnly çerez (`Services/auth_service.py`).
- **Onboarding:** `Services/setup_service.py` + `templates/setup.html` — `.env` + `settings` tablosuna yazan kurulum sihirbazı.

### 0.2 Instagram webhook akışı
```
POST /webhook  (main.instagram_webhook)
  body.object == "instagram"
  entry[0].id            → Instagram Business Account ID  (ALICI işletme hesabı)
  entry[0].messaging[0]:
    sender.id            → IGSID (müşteri)
    recipient.id         → Instagram Business Account ID  (== entry.id)
    message / postback / referral / attachment
```
**Kritik bulgu — canonical tenant routing identifier:**
Instagram'da tenant'ı belirleyen güvenilir kimlik **`entry[].id` (= `recipient.id`) = Instagram Business Account ID**'dir.
Bu, WhatsApp'taki `phone_number_id` routing'inin Instagram karşılığıdır.
`sender.id` müşterinin IGSID'idir — **tenant anahtarı DEĞİLDİR**.
Şu an kod `entry.id`'yi tamamen yok sayıp global `config.IG_ACCOUNT_ID` ile çalışıyor.

### 0.3 Tenant-owned tablolar (tenant_id eklenecek)
| Tablo | Anahtar | Not |
|---|---|---|
| `conversations` | id | `sender`=IGSID |
| `customers` | `phone` (PK) | `phone` aslında IGSID; PK → `(tenant_id, phone)` |
| `orders` | id | `customer_phone`=IGSID |
| `usage_logs` | id | `sender`=IGSID |
| `settings` | `skey` (PK) | tenant başına ayar; PK → `(tenant_id, skey)` |

### 0.4 Multi-tenancy'yi bozan global state
- `message_service.processed_messages` — süreç-içi dedup dict (dağıtık değil, tenant namespace yok).
- `ikas_service` — modül seviyesi `_token_cache`, `ikas_search_cache`, `ikas_product_cache` + **IKAS credential'ları global config'ten**. Farklı tenant'lar farklı İKAS mağazası → cache/creds çakışması (ciddi izolasyon riski).
- `openai_service.client` — global OpenAI key.
- `instagram_service` / `whatsapp_service` / `media_service` — global IG/WA creds.
- Session key `ig:session:{igsid}` — tenant namespace yok.
- `main.system_prompt` — import anında bir kez kurulur; IBAN gibi tenant'a özel alan enjekte edilir.
- `currency_service.currency_cache` — global; ama USD/TRY evrensel → **tenant-agnostik kalabilir** (istisna).

### 0.5 Tenant-specific vs sistem konfigürasyonu
- **Sistem (platform) — `.env`/sistem config kalır:** `META_APP_ID`, `META_APP_SECRET`, `JWT_SECRET`, `ENCRYPTION_KEY` (yeni), MySQL/Redis, `VERIFY_TOKEN` (platform webhook doğrulama).
- **Tenant — DB'de `tenant_settings`:**
  - *Plaintext:* `IG_ACCOUNT_ID`, `IKAS_STORE_NAME`, `IKAS_CLIENT_ID`, `MODEL_NAME`, `STORE_IBAN`, `STORE_IBAN_NAME`, `STORE_NOTIFY_PHONE`, `WHATSAPP_PHONE_NUMBER_ID`, `IG_API_BASE`, `MAX_PRODUCTS`, `CACHE_TTL`.
  - *Secret (şifreli):* `IG_ACCESS_TOKEN`, `IKAS_CLIENT_SECRET`, `OPENAI_API_KEY`, `WHATSAPP_ACCESS_TOKEN`.

---

## Genel İlkeler
1. **Additive & rollback-safe migration.** Önce nullable ekle, backfill et, doğrula, sonra NOT NULL.
2. **Fail-closed.** Tenant çözülemezse mesaj işlenmez (default tenant'a düşürülmez).
3. **Merkezî izolasyon.** Her sorguya elle `tenant_id` serpme; ORM/session katmanında zorunlu kıl.
4. **Geriye dönük uyum.** Mevcut Mumi mağazası `tenant_id=1` altında kesintisiz çalışır.
5. **Incremental.** Her faz sonunda: özet → migration → mevcut testler → yeni testler → regresyon → güvenlik etkisi. Faz başarısızsa sonrakine geçme.
6. **Sır sızdırma yok.** Token/secret asla log/response/exception'a düşmez.

---

## Faz 1 — Tenant Çekirdeği + Kripto + Merkezî İzolasyon
**Amaç:** `Tenant`/`User`/`TenantSetting` modelleri; tüm tenant-owned modellere `tenant_id`; Fernet ile secret şifreleme; tenant contextvar; **otomatik filtreleyen ve INSERT'te tenant_id damgalayan** `get_session()`.

**Değiştirilecek/oluşturulacak dosyalar:**
- `Services/models.py` — `Tenant`, `User`, `TenantSetting` + tüm tablolara `tenant_id`.
- `Services/db.py` — `current_tenant` contextvar, `tenant_scope()`, scoped `get_session()` (varsayılan filtreli; `scoped=False` bypass), `before_flush` (stamp) + `do_orm_execute`/`with_loader_criteria` (filter).
- `Services/crypto_service.py` (yeni) — `ENCRYPTION_KEY` master key, `encrypt`/`decrypt`, fail-closed.
- `config.py` — `ENCRYPTION_KEY`, `META_APP_ID/SECRET` (platform) okuma.
- `migrations/` (yeni, idempotent SQL runner) — `0001_tenants_users_settings.sql`, `0002_add_tenant_id.sql`, `0003_backfill_default_tenant.sql`.

**DB değişiklikleri:** yeni `tenants`, `users`, `tenant_settings`; mevcut 5 tabloya `tenant_id INT NULL` (+ index). Backfill `tenant_id=1`. `customers` PK → `(tenant_id, phone)`; `settings` → `(tenant_id, skey)`.

**Risk:** Global filtre yanlış kurulursa ya sızıntı (çok gevşek) ya da mevcut sorgular boş döner (çok sıkı). **Backward-compat:** default tenant scope'u kurulunca tek-tenant davranışı korunur. **Test:** `tests/test_isolation_orm.py` — 2 tenant, çapraz okuma/yazma; secret encrypt/decrypt; unscoped bypass. **Başarı:** A, B'nin conversations/customers/orders/usage/settings kayıtlarını göremiyor; INSERT otomatik damgalanıyor; secret DB'de plaintext değil.

## Faz 2 — Authentication (tenant-aware)
**Amaç:** Email+parola auth; JWT payload `{user_id, tenant_id, role}`; tenant kimliği **auth'tan** çözülür (request param/header/query'den ASLA).
**Dosyalar:** `Services/auth_service.py`, `Services/user_service.py` (yeni), `main.py` (login/deps). **DB:** `users` (Faz 1). **Test:** `tests/test_auth.py` — login, yanlış parola, token→tenant çözümü, süper-admin. **Başarı:** login sonrası her request tenant'ı auth context'ten alır; forged header işe yaramaz.

## Faz 3 — Tenant Settings + Secret Management
**Amaç:** Config erişimlerini tenant-aware accessor'lara çevir; `settings_service` → `tenant_settings` (şifreli secret, whitelist).
**Dosyalar:** `Services/settings_service.py`, `config.py` accessor'ları, `Services/setup_service.py` (tenant'a yazar). **Test:** `tests/test_settings_secrets.py`. **Başarı:** A'nın secret'ı B tarafından okunamaz; secret DB'de şifreli; sistem sırları tenant sırlarından ayrı.

## Faz 4 — Instagram Webhook Tenant Routing (KRİTİK)
**Amaç:** `entry.id`/`recipient.id` (IG Business Account ID) → tenant çözümü. `Services/tenant_service.py` merkezî resolver (Redis cache). Fail-closed: eşleşmezse reddet + güvenli log.
**Dosyalar:** `Services/tenant_service.py` (yeni), `main.py` webhook. **Akış:** webhook → account id → resolve tenant → `tenant_scope` set → business logic → tenant-izole DB/settings/session/AI. **Test:** `tests/test_webhook_routing.py` — A webhook→A, B→B, unknown→reddedilir. **Başarı:** yanlış tenant context'inde işlenmez; unknown default'a gitmez.

## Faz 5 — Session / Cache / State İzolasyonu
**Amaç:** Tüm session/cache key'lerine tenant namespace. Dedup Redis'e. İKAS cache tenant-scoped + creds tenant'tan. OpenAI client tenant key'iyle.
**Dosyalar:** `session_store.py` (`ia:session:{tenant_id}:{igsid}`), `message_service.py` (Redis dedup `ia:dedup:{tenant_id}:{mid}`), `ikas_service.py` (tenant-scoped cache + tenant creds), `openai_service.py` / `media_service.py` / `instagram_service.py` / `whatsapp_service.py` (tenant creds parametreli). **Test:** `tests/test_session_isolation.py` — aynı IGSID iki tenant'ta çakışmaz. **Başarı:** A session/cache/dedup state'i B'de görünmez.

## Faz 6 — AI + Usage Tenant-Aware
**Amaç:** AI çağrıları tenant context'inde (prompt/settings/store/products/history). `usage_logs.tenant_id`. Dashboard AI Usage yalnız aktif tenant.
**Dosyalar:** `openai_service.py`, `usage_logger.py`, `main.py` prompt kurulumu (per-tenant/per-request). **Test:** `tests/test_ai_usage_isolation.py`. **Başarı:** A'nın AI context'ine B'nin konuşma/ürün/prompt'u girmez; usage ayrık.

## Faz 7 — Dashboard / API İzolasyonu (IDOR)
**Amaç:** Tüm `/admin/*` endpoint'leri auth tenant context'iyle. Frontend'in tenant_id'sine güvenme. IDOR testleri.
**Dosyalar:** `main.py` (deps + endpoint'ler), `dashboard_service.py` (scoped session). **Test:** `tests/test_idor.py` — A, B'nin conversation/customer/lead/resource ID'siyle veri alamaz. **Başarı:** çapraz resource erişimi 404/boş.

## Faz 8 — Onboarding
**Amaç:** `tenant → owner user → tenant settings → Instagram → store → AI → ready` atomik akışı. Duplicate email kontrolü, orphan tenant yok. Super-admin üzerinden tenant oluşturma (public signup şart değil).
**Dosyalar:** `Services/onboarding_service.py` (yeni), `main.py`. **Test:** `tests/test_onboarding.py`. **Başarı:** atomik oluşturma; setup ekranı yeniden kullanılır.

## Faz 9 — Meta / Instagram Connection (OAuth)
**Amaç:** Tenant kendi IG Business hesabını bağlar. Platform: `META_APP_ID/SECRET`, redirect. Tenant: account id/token → tenant_settings (şifreli). OAuth `state`: tahmin edilemez, kısa ömürlü, tek kullanımlık, tenant/user'a bağlı. Callback başka tenant'ın connection'ını ezemez.
**Dosyalar:** `Services/meta_oauth_service.py` (yeni), `main.py`. **Test:** `tests/test_oauth_state.py`. **Başarı:** state doğrulaması; token loglanmaz; cross-tenant overwrite engellenir.

## Faz 10 — DB Hardening
**Amaç:** `tenant_id NOT NULL` (backfill+doğrulama sonrası). Composite uniqueness: `UNIQUE(tenant_id, phone)`, `UNIQUE(tenant_id, skey)`, `tenants.ig_account_id UNIQUE` (Meta çapında global). FK + index.
**Dosyalar:** `migrations/0004_*`, `0005_*`. **Test:** migration idempotent + isolation testleri hâlâ geçer. **Başarı:** şema kısıtları izolasyonu DB seviyesinde de garanti eder.

## Faz 11 — Docker / Production
**Amaç:** Tek deployment (app + MySQL + Redis). Container/tenant değil. Env yalnız sistem config. Healthcheck/migration/startup. **Dosyalar:** `docker-compose.yml`, `Dockerfile`, entrypoint migration. **Başarı:** tek stack çok tenant'a hizmet verir.

## Faz 12 — Billing Hazırlığı (uygulanmaz)
Tenant modeli `plan`, `status`, `quota` için genişletilebilir bırakılır. Şimdi kod yazılmaz.

---

## Test Stratejisi (zorunlu)
- Ortam: `.venv` + **SQLite** (SQLAlchemy dialect-bağımsız modeller) + **fakeredis**.
- `tests/conftest.py` — in-memory DB, `Base.metadata.create_all`, 2 tenant fixture (A, B).
- Güvenlik test matrisi (hepsi geçmeden dönüşüm "tamam" sayılmaz):
  1. A, B verisini okuyamaz / B, A'yı okuyamaz (her tablo).
  2. A webhook→A, B webhook→B; unknown account hiçbir tenant'a gitmez.
  3. A session/cache/dedup state'i B'de görünmez.
  4. A API credential'ı B tarafından okunamaz (secret şifreli).
  5. A kullanıcısı B resource ID'siyle endpoint'ten veri alamaz (IDOR).
  6. Unscoped session yanlışlıkla business endpoint'te kullanılmıyor.

## Riskler & Azaltma
- **En büyük risk:** global ORM filtresinin bir modelde atlanması → sızıntı. Azaltma: allowlist tabanlı `TenantOwned` mixin + testler her tablo için.
- **Regresyon:** mevcut Mumi akışı bozulur. Azaltma: `tenant_id=1` default; her fazdan sonra regresyon.
- **Sır sızıntısı:** log/exception. Azaltma: crypto fail-closed + redaction (setup_service'te mevcut desen).

## Çıktı Belgeleri
- Bu dosya: `INSTAAGENT_SAAS_ROADMAP.md`.
- Bitişte: `INSTAAGENT_SAAS_IMPLEMENTATION.md` (önceki/yeni mimari, tenant resolution, auth, webhook, secrets, şema, migration listesi, Redis, izolasyon, onboarding, Meta connection, güvenlik testleri, deployment, rollback, teknik borç).
