# InstaAgent — Multi-Tenant SaaS Dönüşümü: Uygulama Raporu

> Kapsam: Faz 1–11 uygulandı ve **51 otomatik test** ile doğrulandı (izolasyon
> dahil). Testler `.venv` + SQLite ile çalışır: `./.venv/Scripts/python.exe -m pytest tests/`.
> Faz 12 (billing) bilinçli olarak kapsam dışıdır; mimari genişletilebilir bırakıldı.

---

## 1. Önceki mimari → Yeni mimari

| Konu | Önce (tek-tenant) | Sonra (multi-tenant) |
|---|---|---|
| Veri erişimi | Karışık: ORM okuma + bazı ham SQL yazma | Tüm tenant-owned yazma/okuma **scoped ORM** |
| İzolasyon | Yok | Session/ORM seviyesinde **merkezî ve otomatik** |
| Kimlik | Tek `.env` kullanıcısı, JWT `sub` | Email+parola (DB `users`), JWT `{tid,uid,role}` |
| Tenant çözümü | Yok (global `IG_ACCOUNT_ID`) | Webhook `entry.id` → `tenant_service` |
| Sırlar | `.env` düz metin | `tenant_settings`'te **Fernet şifreli** |
| Session/cache | `ig:session:{igsid}`, global dict'ler | `ia:session:{tenant}:{igsid}`, tenant-namespaced |
| Kredensiyeller | Global config sabitleri | Tenant ayarından (fallback `.env`) |

## 2. Tenant resolution (webhook routing)

Canonical anahtar: **Instagram Business Account ID = webhook `entry[].id` (= `recipient.id`)**
— WhatsApp'taki `phone_number_id`'nin Instagram karşılığı. `sender.id` müşterinin
IGSID'idir, tenant anahtarı DEĞİLDİR.

```
POST /webhook → extract_ig_account_id(body) → resolve_tenant_by_ig_account_id()
  ├─ eşleşme yok → {status: ignored, reason: unknown_account}  (FAIL-CLOSED)
  └─ tenant_id → with tenant_scope(tenant_id): _process_instagram_webhook(body)
```
`Services/tenant_service.py`: `tenants.ig_account_id` üzerinden çözer, kısa ömürlü
süreç-içi TTL cache (kaynak gerçeği DB), `invalidate()` ile temizlenir. Bilinmeyen/pasif
hesap **asla** default tenant'a düşmez.

## 3. Auth akışı
`Services/auth_service.py` + `Services/user_service.py`:
- `authenticate(email, password)` → DB kullanıcısı (bcrypt) → ctx `{user_id, tenant_id, email, role}`; bulunamazsa legacy `.env` kullanıcısı (tenant 1).
- `create_token(ctx)` → JWT `{sub, uid, tid, role}`. `verify_token()` → ctx (yoksa None).
- **Tenant kimliği yalnız imzalı token'dan çözülür.** `main.require_dashboard_auth`
  (async generator dependency) her panel isteğinde `tenant_scope`'u JWT'den kurar; istek
  bitince geri alır. Böylece panel sorguları otomatik izole olur.

## 4. Instagram webhook flow (özet)
`entry.id → tenant → tenant_scope → cleanup/parse → dedup(tenant-ns) → session(tenant-ns)
→ İKAS(tenant creds+cache) → OpenAI(tenant key) → log_message/save_order/log_usage(auto tenant_id)
→ send_instagram_message(tenant creds)`.

## 5. Settings / secrets
`Services/settings_service.py` (scoped ORM) + `Services/crypto_service.py` (Fernet):
- `SECRET_SETTING_KEYS` = {IG_ACCESS_TOKEN, IKAS_CLIENT_SECRET, OPENAI_API_KEY, WHATSAPP_ACCESS_TOKEN} → DB'de `enc:v1:...` şifreli.
- `get_stored_setting` tekil okumada secret'ı çözer; `get_all_stored_settings` ham (şifreli) döndürür (toplu okuma sır sızdırmaz).
- **Sistem sırları** (ENCRYPTION_KEY, META_APP_*, JWT_SECRET, MySQL) `.env`/sistem config'inde; tenant_settings'e YAZILMAZ. `config.py` tenant-aware accessor'lar (`ig_access_token()`, `openai_api_key()`, …) tenant ayarını, yoksa `.env` fallback'ini okur.

## 6. DB şema değişiklikleri
Yeni kök modeller: `tenants`, `users`, `oauth_states`.
Tenant-owned tablolara `tenant_id`: `usage_logs, conversations, orders, customers, settings`.
Bileşik anahtar: `customers (tenant_id, phone)`, `settings (tenant_id, skey)`.
`tenants.ig_account_id` **UNIQUE** (Meta çapında global). `TENANT_OWNED_MODELS` allowlist'i
filtreyi sürer (`Services/models.py`).

## 7. Migration listesi (`migrations/run.py`)
- `apply` (idempotent): `create_all` (temiz kurulum + eksik tablolar) → MySQL'de mevcut
  tablolara `tenant_id` ekle → default tenant (Mumi, id=1) oluştur → mevcut satırları
  `tenant_id=1` backfill. Container başlangıcında otomatik çalışır (`docker-entrypoint.sh`).
- `harden` (Faz 10, doğrulama SONRASI, MySQL): `tenant_id NOT NULL` + `customers/settings`
  bileşik PK. NULL kalan satır varsa durur (fail-safe).

## 8. Redis / session yapısı
- `Services/session_store.py`: `SessionRegistry` anahtarları `{tenant}:{igsid}`; Redis key `ia:session:{tenant}:{igsid}`. Aynı IGSID farklı tenant'larda çakışmaz.
- Dedup (`Services/message_service.py`): `ia:dedup:{tenant}:{mid}`, Redis `SET NX EX` (dağıtık-güvenli), yoksa namespaced bellek.
- İKAS cache (`ikas_service`): token/arama/ürün cache'leri `(tenant, …)` demetiyle namespaced; creds tenant'tan.

## 9. İzolasyon mekanizması (merkezî)
`Services/db.py`:
- `current_tenant_id` contextvar + `tenant_scope()`.
- `get_session(scoped=True)` (varsayılan): `do_orm_execute` olayı `TENANT_OWNED_MODELS`
  için `WHERE tenant_id = <aktif>` **doğrudan ifade** olarak ekler (lambda önbellek
  açığından kaçınmak kritik — bkz. §13). `before_flush` yeni kayıtlara tenant_id
  damgalar, çapraz-tenant insert/update'i reddeder.
- Scoped ama tenant yok → **fail-closed** (hiç satır). `scoped=False` → bilinçli bypass
  (login, tenant resolution, onboarding, migration).
- Tek-tenant köprüsü: scope yoksa `DEFAULT_TENANT_ID=1` (Faz 10'da `set_default_tenant_fallback(False)` ile kapatılabilir).

## 10. Onboarding (`Services/onboarding_service.py`)
`create_tenant(name, owner_email, owner_password, ig_account_id?, initial_settings?)`
tek transaction'da tenant + owner user (+ şifreli ayarlar) oluşturur — **atomik**,
duplicate email / duplicate IG hesabı reddi, orphan bırakmaz. `create_superadmin()`
platform operatörü bootstrap'ı. Endpoint: `POST /admin/platform/tenants` (super-admin).

## 11. Meta connection (`Services/meta_oauth_service.py`)
OAuth `state`: tahmin edilemez (`secrets.token_urlsafe(32)`), kısa ömürlü (10 dk),
**tek kullanımlık** (consume→delete), tenant/user'a bağlı (`oauth_states`). `handle_callback`
state'i doğrular, token'ı **doğru tenant'a şifreli** yazar; hedef IG hesabı başka tenant'a
bağlıysa reddeder (cross-tenant overwrite yok). Token/secret loglanmaz. Endpoint'ler:
`GET /admin/connect/instagram`, `GET /connect/instagram/callback`.

## 12. Güvenlik testleri (51 test / hepsi geçti)
| Dosya | Kanıt |
|---|---|
| `test_isolation_orm.py` (9) | Her tabloda çapraz okuma engeli, auto-stamp, çapraz insert reddi, fail-closed, secret şifreleme |
| `test_settings_secrets.py` (4) | Secret at-rest şifreli, tenant izolasyonu, upsert |
| `test_auth.py` (7) | Tenant token'dan çözülür, forge edilemez, duplicate email, legacy fallback |
| `test_webhook_routing.py` (6) | A→A, B→B, unknown→fail-closed, çapraz sızma yok |
| `test_session_isolation.py` (3) | Session/dedup/İKAS cache tenant-namespaced |
| `test_ai_usage_isolation.py` (3) | usage_logs damga + dashboard AI usage izole |
| `test_idor.py` (6) | A, B'nin resource ID'siyle veri alamaz; query'deki tenant_id yok sayılır |
| `test_onboarding.py` (5) | Atomik oluşturma, duplicate reddi, izolasyon |
| `test_oauth_state.py` (6) | State tek-kullanımlık/süreli/bağlı; cross-tenant overwrite reddi |
| `test_migration.py` (2) | Temiz kurulum + idempotency + backfill köprüsü |

## 13. Kritik bulgu (regression önlendi)
`with_loader_criteria`'ya **lambda** verildiğinde, closure/argüman önbelleğe alınıp ilk
tenant'ın filtresi ikinci tenant sorgusunda yeniden kullanılıyordu → **gerçek çapraz-tenant
sızıntısı**. Testler yakaladı; filtre **doğrudan ifade** (`model.tenant_id == tid`) ile
her istekte taze bağlanacak şekilde düzeltildi.

## 14. Production deployment notları
- Tek deployment: app + MySQL + Redis (compose). Container/tenant DEĞİL.
- `docker-entrypoint.sh` başlangıçta `migrations.run apply` çalıştırır (MySQL healthy sonrası).
- `.env` yalnız **sistem** config'i içerir: `ENCRYPTION_KEY` (Fernet), `META_APP_ID/SECRET/REDIRECT_URI`, `JWT_SECRET`, MySQL/Redis, `VERIFY_TOKEN`. Tenant kredensiyelleri DB'de şifreli.
- `ENCRYPTION_KEY` kaybı = tenant sırlarının kaybı; güvenli yedekleyin, rotasyonda v2 planlayın.

## 15. Rollback prosedürü
- Kod: bu değişiklikler additive'dir. `apply` NOT NULL yapmaz; `harden` çalıştırılmadıkça
  şema eski kodla da uyumludur (fazladan `tenant_id`/tablo eski kodu bozmaz).
- Acil geri dönüş: önceki imaja dön. Veri korunur (tenant_id=1 = Mumi).
- `harden` sonrası geri almak isterseniz: bileşik PK'yı tek sütuna, NOT NULL'ı NULL'a
  çeken ters ALTER'lar gerekir (yalnız tek tenant kaldıysa güvenli).

## 16. Kalan teknik borçlar
- **Setup sihirbazı** (`setup_service.py`) hâlâ IG/İKAS/OpenAI creds'ini `.env`'e yazıyor
  (varsayılan tenant/sistem fallback'i). Tenant-başına panel setup'ı bunları
  `tenant_settings`'e yazacak şekilde uyarlanmalı (mekanizma hazır: settings_service +
  OAuth). İzolasyonu bozmaz; yeni tenant'lar OAuth/onboarding ile ayarlanır.
- `_setup_gate` middleware ve `setup_service._setup_complete_cache` süreç-global; per-tenant
  setup gating eklenmeli (yalnız kurulum yönlendirmesini etkiler, veriyi değil).
- Tenant resolver cache süreç-içi; çok-instance'ta Redis'e taşınabilir (opsiyonel; kaynak DB).
- OpenAI/İKAS client cache anahtar değişiminde `invalidate_*` çağrılmalı (setup/OAuth kaydında).
- `migrations.run harden` ve MySQL ALTER yolları gerçek MySQL'de duman testinden geçirilmeli
  (birim testler SQLite `create_all` ile hedef şemayı doğrular).
- Faz 12 (billing): `tenants.plan/status` alanları hazır; quota/subscription eklenebilir.
