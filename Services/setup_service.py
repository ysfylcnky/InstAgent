"""Kurulum (Setup) servisi — SaaS onboarding'in backend mantığı.

Tasarım kuralı (mimariyi bozmadan, minimum müdahale):
  * Secret / import-time okunan tüm değerler .env'e yazılır (dotenv.set_key);
    uygulanması sunucu yeniden başlatılınca olur. Böylece servis dosyalarının
    içi hiç değişmez (whatsapp/ikas/openai vb. dokunulmaz).
  * Zaten dinamik okunan alanlar (STORE_IBAN, STORE_IBAN_NAME) ve kurulum
    durumu (SETUP_*, *_TESTED_AT) mevcut `settings` tablosuna yazılır
    (settings_service). IBAN değişimi main.py'de reload_system_prompt() ile
    anında geçerli olur.
  * Test fonksiyonları posted (henüz kaydedilmemiş olabilecek) değerlerle
    KENDİ KENDİNE yeterli çalışır; import-time sabitlere / restart'a bağlı
    değildir. Böylece kullanıcı kaydetmeden önce doğrulayabilir.

Yeni bağımlılık eklenmez: python-dotenv ve requests zaten kuruludur.
"""

import os
import re
from datetime import datetime

import requests
from dotenv import dotenv_values, find_dotenv

from Services.usage_logger import get_connection
from Services.settings_service import (
    get_all_stored_settings,
    save_stored_settings,
)


# --------------------------------------------------------------------------
# .env yolu — çalışma dizininden bulunur; yoksa proje kökündeki .env varsayılır.
# --------------------------------------------------------------------------
def _env_path():
    found = find_dotenv(usecwd=True)
    if found:
        return found
    # Services/ -> proje kökü
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, ".env")


ENV_PATH = _env_path()


def _ensure_env_file():
    """Yazmadan önce .env'in var olduğundan emin ol (ilk kurulum)."""
    if not os.path.exists(ENV_PATH):
        open(ENV_PATH, "a", encoding="utf-8").close()


# --------------------------------------------------------------------------
# .env'i YERİNDE güncelle — rename YOK.
# .env tek dosya olarak konteynere bind-mount edildiğinde (docker-compose'daki
# `./.env:/app/.env`), dotenv.set_key gibi "geçici dosya + rename" yöntemiyle
# yazmak "[Errno 16] Device or resource busy" verir: mount noktasının üzerine
# rename yapılamaz. Bu yüzden dosya açılıp içerik AYNI inode'a yeniden yazılır.
# --------------------------------------------------------------------------
_ENV_SAFE_VALUE = re.compile(r"[A-Za-z0-9_./:@+=-]*")


def _env_format_value(value):
    """Değeri dotenv-uyumlu biçimler: özel karakter yoksa çıplak, varsa tek tırnak.

    Tek tırnaklı değer dotenv'de LİTERAL okunur ('$' genişletmesi olmaz); bu da
    bcrypt hash'i gibi '$' içeren değerler için güvenlidir.
    """
    if value == "" or _ENV_SAFE_VALUE.fullmatch(value):
        return value
    return "'" + value.replace("'", "") + "'"


def _set_env_in_place(path, key, value):
    """`.env`'de KEY=VALUE satırını yerinde günceller ya da ekler (rename yok)."""
    new_line = f"{key}={_env_format_value(value)}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []

    pattern = re.compile(r"\s*" + re.escape(key) + r"\s*=")
    out = []
    replaced = False

    for line in lines:
        is_key_line = not line.lstrip().startswith("#") and pattern.match(line)
        if is_key_line:
            if not replaced:
                out.append(new_line)
                replaced = True
            # aynı anahtarın olası tekrarlarını at
            continue
        out.append(line)

    if not replaced:
        out.append(new_line)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# --------------------------------------------------------------------------
# Alan şeması — her bölüm ve alanın tipi/zorunluluğu/hedefi.
#   target: "env"     -> .env'e yazılır (restart ile geçerli)
#           "setting" -> settings tablosuna yazılır (anında geçerli olabilir)
#           "readonly"-> gösterilir ama ASLA bu endpoint'ten yazılmaz (ör. MySQL)
# --------------------------------------------------------------------------
SECTIONS = [
    {
        "id": "company", "required": True, "test": False,
        "fields": {
            "STORE_NAME":      {"type": "text", "target": "setting"},
            "STORE_IBAN":      {"type": "iban", "target": "setting"},
            "STORE_IBAN_NAME": {"type": "text", "target": "setting"},
        },
    },
    {
        "id": "instagram", "required": True, "test": True,
        "fields": {
            # MÜŞTERİYE ait bağlantı bilgileri → DB (settings). Her mağaza kendi
            # Instagram hesabını bağlar; değişiklik anında geçerli olur (accessor).
            "IG_ACCOUNT_ID":   {"type": "digits", "required": True, "target": "setting"},
            "IG_ACCESS_TOKEN": {"type": "text", "required": True, "secret": True, "target": "setting"},
            # Bağlantı türü: Instagram Login (graph.instagram.com) ya da bağlı
            # Facebook Sayfası (graph.facebook.com). Boşsa varsayılana düşülür.
            "IG_API_BASE":     {"type": "choice", "target": "setting",
                                "choices": ["graph.facebook.com", "graph.instagram.com"]},
        },
    },
    # NOT: OpenAI (OPENAI_API_KEY, MODEL_NAME) ve VERIFY_TOKEN SİSTEM (platform)
    # değerleridir; müşteriye sorulmaz, .env'den okunur (META_APP_* gibi). Bu yüzden
    # kurulum sihirbazında AYRI bir "ai" bölümü yoktur.
    {
        "id": "ikas", "required": True, "test": True,
        "fields": {
            "IKAS_STORE_NAME":   {"type": "slug", "required": True, "target": "setting"},
            "IKAS_CLIENT_ID":    {"type": "text", "required": True, "target": "setting"},
            "IKAS_CLIENT_SECRET": {"type": "text", "required": True, "secret": True, "target": "setting"},
        },
    },
    {
        "id": "product", "required": False, "test": True,
        "fields": {
            "MAX_PRODUCTS": {"type": "number", "target": "env", "min": 1, "max": 10},
            "CACHE_TTL":    {"type": "number", "target": "env", "min": 60, "max": 3600},
        },
    },
    {
        # Mağaza bildirimi WhatsApp üzerinden gider (müşteri Instagram'dan gelse de).
        # Opsiyoneldir: boşsa sipariş bildirimi atlanır, müşteri akışı etkilenmez.
        "id": "notify", "required": False, "test": True,
        "fields": {
            "WHATSAPP_PHONE_NUMBER_ID": {"type": "digits", "target": "setting"},
            "WHATSAPP_ACCESS_TOKEN":    {"type": "text", "secret": True, "target": "setting"},
            "STORE_NOTIFY_PHONE":       {"type": "phone", "target": "setting"},
        },
    },
    {
        "id": "advanced", "required": False, "test": False,
        "fields": {
            # Panel girişi TENANT'A AİTTİR → users tablosu (target="account").
            # .env'de tutulamaz: ortak dosya olduğu için ikinci bir tenant
            # birincinin giriş bilgisini ezerdi. Bkz. user_service.upsert_tenant_owner.
            "PANEL_EMAIL":    {"type": "email", "target": "account"},
            "PANEL_PASSWORD": {"type": "text", "secret": True, "target": "account", "min_len": 8},
            "MYSQL_HOST":     {"type": "text", "target": "readonly"},
            "MYSQL_PORT":     {"type": "number", "target": "readonly"},
            "MYSQL_USER":     {"type": "text", "target": "readonly"},
            "MYSQL_PASSWORD": {"type": "text", "secret": True, "target": "readonly"},
            "MYSQL_DATABASE": {"type": "text", "target": "readonly"},
        },
    },
]

# Kurulumun "tamamlandı" sayılması için AKTİF TENANT'ın settings kayıtlarında
# dolu olması gereken MÜŞTERİ anahtarları (her mağaza kendi kurulumunu yapar).
# Secret'lar DB'de şifreli olsa da "dolu" sayılır. (STORE_NAME gibi kozmetik
# alanlar bloklamaz.) OpenAI/VERIFY_TOKEN gibi SİSTEM anahtarları burada YOKTUR —
# onlar operatörün .env sorumluluğudur, müşteri kurulumunu bloklamaz.
REQUIRED_SETTING_KEYS = [
    "IG_ACCOUNT_ID", "IG_ACCESS_TOKEN",
    "IKAS_STORE_NAME", "IKAS_CLIENT_ID", "IKAS_CLIENT_SECRET",
]

# Kurulum tamamlanması yalnız müşteri (tenant) ayarlarına bakar. Sistem sırları
# (OPENAI_API_KEY, VERIFY_TOKEN, META_APP_*, ENCRYPTION_KEY, MySQL/Redis) operatör
# tarafından .env'de bir kez yapılandırılır; müşteri kurulumunu GATE'lemez.
REQUIRED_ENV_KEYS = []


def _section(section_id):
    for s in SECTIONS:
        if s["id"] == section_id:
            return s
    return None


# --------------------------------------------------------------------------
# Okuma / durum
# --------------------------------------------------------------------------
def _db_ok():
    conn = None
    try:
        conn = get_connection()
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _current_value(key, meta, env_vals, stored):
    if meta["target"] == "setting":
        return stored.get(key)
    if meta["target"] == "account":
        # Panel girişi users tablosunda. Parola asla geri okunmaz; email
        # gösterilir ki merchant hangi adresle girdiğini görebilsin.
        if key != "PANEL_EMAIL":
            return None
        try:
            from Services.user_service import get_tenant_owner
            owner = get_tenant_owner(_tenant_key())
            return owner.email if owner is not None else None
        except Exception:
            return None
    return env_vals.get(key) or os.getenv(key)


def _section_status(sec, fields_out, tested_at):
    for f in fields_out:
        if f["required"] and not f["set"]:
            return "missing"
    if sec.get("test") and not tested_at:
        return "untested"
    return "ok"


# Tek yönlü mandal — ARTIK TENANT BAZLI. Bir tenant kurulumu tamamlanınca o
# tenant için süreç ömrü boyunca True kalır (başka tenant'ı ETKİLEMEZ). Böylece
# tamamlanmış panelde her istekte DB'ye gidilmez ve geçici DB kesintisi
# kullanıcıyı Kurulum ekranına düşürmez (kurulum geri alınmaz).
_setup_complete_cache = {}  # tenant_id -> True


def reset_setup_cache(tenant_id=None):
    """Kurulum-tamamlandı mandalını sıfırlar (belirli tenant ya da tümü).

    Kredensiyel/kurulum değişiminde ya da testlerde çağrılır.
    """
    if tenant_id is None:
        _setup_complete_cache.clear()
    else:
        _setup_complete_cache.pop(tenant_id, None)


def _tenant_key():
    """Aktif tenant kimliği (cache namespace'i). Çözülemezse None."""
    try:
        from Services.db import get_current_tenant
        return get_current_tenant()
    except Exception:
        return None


def is_setup_complete(env_vals=None, stored=None, db_ok=None):
    """AKTİF TENANT için kurulum tamam mı: DB erişilebilir + zorunlu tenant
    ayarları dolu + platform (.env) zorunluları dolu + SETUP_COMPLETED=1.

    Zorunlu tenant credential'ları .env'de DEĞİL, aktif tenant'ın settings
    kayıtlarında aranır (per-tenant kurulum). Bir tenant'ın tamamlanması
    başkasını "tamam" yapmaz.
    """
    tid = _tenant_key()
    if _setup_complete_cache.get(tid):
        return True

    if env_vals is None:
        env_vals = dotenv_values(ENV_PATH)
    if stored is None:
        stored = get_all_stored_settings()
    if db_ok is None:
        db_ok = _db_ok()

    if not db_ok:
        return False

    # Platform (.env) zorunluları
    for k in REQUIRED_ENV_KEYS:
        v = env_vals.get(k) or os.getenv(k)
        if v is None or str(v).strip() == "":
            return False

    # Tenant (DB settings) zorunluları — secret'lar şifreli ama DOLU sayılır.
    for k in REQUIRED_SETTING_KEYS:
        v = stored.get(k)
        if v is None or str(v).strip() == "":
            return False

    complete = str(stored.get("SETUP_COMPLETED", "")).strip() == "1"
    if complete:
        _setup_complete_cache[tid] = True
    return complete


def get_setup_state():
    """Tüm bölümlerin alan durumları + statü + genel tamamlanma bilgisi (JSON)."""
    env_vals = dotenv_values(ENV_PATH)
    stored = get_all_stored_settings()
    db_ok = _db_ok()

    sections_out = []
    for sec in SECTIONS:
        fields_out = []
        for key, meta in sec["fields"].items():
            raw = _current_value(key, meta, env_vals, stored)
            is_set = raw is not None and str(raw).strip() != ""
            field = {
                "key": key,
                "type": meta["type"],
                "required": bool(meta.get("required")),
                "secret": bool(meta.get("secret")),
                "target": meta["target"],
                "set": is_set,
                # Secret değerler asla geri gönderilmez; sadece "kayıtlı mı" bilgisi
                "value": None if meta.get("secret") else (raw if is_set else None),
            }
            for extra in ("min", "max", "min_len", "choices"):
                if extra in meta:
                    field[extra] = meta[extra]
            fields_out.append(field)

        tested_at = stored.get(sec["id"].upper() + "_TESTED_AT") if sec.get("test") else None
        sections_out.append({
            "id": sec["id"],
            "required": sec["required"],
            "test": bool(sec.get("test")),
            "status": _section_status(sec, fields_out, tested_at),
            "tested_at": tested_at,
            "fields": fields_out,
        })

    return {
        "completed": is_setup_complete(env_vals, stored, db_ok),
        "db_ok": db_ok,
        "sections": sections_out,
    }


# --------------------------------------------------------------------------
# Doğrulama
# --------------------------------------------------------------------------
def _validate(key, meta, value):
    value = "" if value is None else str(value).strip()

    if meta.get("required") and value == "":
        return f"{key} zorunludur."
    if value == "":
        return None  # opsiyonel ve boş — sorun yok

    if meta.get("choices") and value not in meta["choices"]:
        return f"{key} için geçerli bir seçenek seçin."

    t = meta["type"]
    if t == "digits" and not re.fullmatch(r"\d{6,25}", value):
        return f"{key} yalnızca rakamlardan oluşmalı."
    if t == "phone" and not re.fullmatch(r"\d{10,15}", value):
        return "Telefon ülke koduyla ve yalnız rakam olmalı (10-15 hane)."
    if t == "slug" and not re.fullmatch(r"[a-z0-9-]+", value):
        return "Mağaza adı yalnız küçük harf, rakam ve tire içerebilir."
    if t == "token" and re.search(r"\s", value):
        return "Verify token boşluk içeremez."
    if t == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        return "Geçerli bir e-posta adresi girin."
    if t == "iban":
        v = value.replace(" ", "").upper()
        if not re.fullmatch(r"TR\d{24}", v):
            return "IBAN 'TR' + 24 rakamdan oluşmalı."
    if t == "number":
        try:
            n = float(value.replace(",", "."))
        except ValueError:
            return f"{key} sayı olmalı."
        if "min" in meta and n < meta["min"]:
            return f"{key} en az {meta['min']} olmalı."
        if "max" in meta and n > meta["max"]:
            return f"{key} en çok {meta['max']} olmalı."
    if meta.get("min_len") and len(value) < meta["min_len"]:
        return f"{key} en az {meta['min_len']} karakter olmalı."
    return None


# --------------------------------------------------------------------------
# Kaydetme (bölüm bazlı)
# --------------------------------------------------------------------------
def save_section(section_id, fields):
    sec = _section(section_id)
    if not sec:
        return {"ok": False, "error": "Bilinmeyen bölüm."}
    if not isinstance(fields, dict):
        return {"ok": False, "error": "Geçersiz gövde."}

    env_writes = {}
    setting_writes = {}
    account_writes = {}   # panel girişi → users tablosu (tenant'a ait)
    restart_required = False

    for key, meta in sec["fields"].items():
        if meta["target"] == "readonly":
            continue  # ör. MySQL — çalışan uygulamanın DB'sini web'den bozmayı engelle
        if key not in fields:
            continue

        raw = fields[key]
        val = "" if raw is None else str(raw).strip()

        # Secret alan boş bırakıldıysa mevcut kayıtlı değer korunur
        if meta.get("secret") and val == "":
            continue

        err = _validate(key, meta, val)
        if err:
            return {"ok": False, "error": err}

        if meta["type"] == "iban" and val != "":
            val = val.replace(" ", "").upper()
        if meta["type"] == "number" and val != "":
            n = float(val.replace(",", "."))
            val = str(int(n)) if n == int(n) else str(n)

        if meta["target"] == "setting":
            setting_writes[key] = val
        elif meta["target"] == "account":
            account_writes[key] = val
        else:
            env_writes[key] = val
            restart_required = True

    # Koşullu kural: IBAN girildiyse IBAN adı da olmalı
    if section_id == "company":
        stored = get_all_stored_settings()
        iban = setting_writes.get("STORE_IBAN", stored.get("STORE_IBAN") or "")
        name = setting_writes.get("STORE_IBAN_NAME", stored.get("STORE_IBAN_NAME") or "")
        if str(iban).strip() and not str(name).strip():
            return {"ok": False, "error": "IBAN girildiğinde IBAN Ad Soyad da zorunludur."}

    # IG hesap kimliği webhook routing'in anahtarıdır (Tenant.ig_account_id sütunu).
    # Setup yalnız settings'e yazarsa tenant kurulumu bitse bile webhook'lar
    # eşleşmez; bu yüzden routing sütununu da senkronla (çakışma varsa reddet).
    if "IG_ACCOUNT_ID" in setting_writes:
        err = _sync_ig_account_id_to_tenant(setting_writes["IG_ACCOUNT_ID"])
        if err:
            return {"ok": False, "error": err}

    if setting_writes:
        if not save_stored_settings(setting_writes):
            return {"ok": False, "error": "Ayar kaydedilemedi (DB erişilemiyor olabilir)."}

    # Panel girişi tenant'a aittir → users tablosu. Parola bcrypt ile hash'lenir
    # (upsert_tenant_owner içinde); düz metin hiçbir yere yazılmaz. .env'e panel
    # kullanıcısı YAZILMAZ — ortak dosya olduğu için tenant'lar birbirini ezerdi.
    if account_writes:
        try:
            from Services.user_service import upsert_tenant_owner
            upsert_tenant_owner(
                _tenant_key(),
                email=account_writes.get("PANEL_EMAIL") or None,
                password=account_writes.get("PANEL_PASSWORD") or None,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"Panel girişi kaydedilemedi: {e}"}

    if env_writes:
        try:
            _ensure_env_file()
            for k, v in env_writes.items():
                _set_env_in_place(ENV_PATH, k, v)
        except Exception as e:
            return {"ok": False, "error": f".env yazılamadı: {e}"}

    saved_keys = (list(setting_writes.keys()) + list(env_writes.keys())
                  + list(account_writes.keys()))
    _invalidate_caches_for_saved(saved_keys)

    return {
        "ok": True,
        "restart_required": restart_required,
        "saved": saved_keys,
    }


def _sync_ig_account_id_to_tenant(ig_account_id):
    """Setup ile girilen IG hesap kimliğini tenant kaydına (routing anahtarı) yazar.

    Webhook routing `Tenant.ig_account_id` sütununu kullanır. Başka bir tenant'a
    bağlı bir hesap girilirse çapraz-ele geçirmeyi önlemek için hata (mesaj) döner;
    aksi halde None döner. Cross-tenant sistem işi → scoped=False.
    """
    ig = str(ig_account_id or "").strip()
    tenant = _tenant_key()
    if not ig or tenant is None:
        return None

    from sqlalchemy import select
    from Services.db import get_session
    from Services.models import Tenant

    try:
        with get_session(scoped=False) as s:
            other = s.execute(
                select(Tenant).where(
                    Tenant.ig_account_id == ig, Tenant.id != tenant
                )
            ).scalar_one_or_none()
            if other is not None:
                return "Bu Instagram hesap ID'si zaten başka bir mağazaya bağlı."
            row = s.get(Tenant, tenant)
            if row is not None:
                row.ig_account_id = ig
    except Exception as e:
        print("🔴 IG hesap kimliği tenant'a yazılamadı:", e)
        return "Instagram hesap kimliği kaydedilemedi (DB)."
    return None


def _invalidate_caches_for_saved(saved_keys):
    """Kredensiyel yazımı sonrası ilgili TENANT-SCOPED cache'leri tazeler (Faz B3).

    Yeni anahtar/mağaza yazıldığında eski client/token/ürün önbelleği kullanılmaya
    devam etmesin. İlgili servisin cache'i sessizce (fail-safe) temizlenir.
    """
    keys = set(saved_keys or [])
    tenant = _tenant_key()

    # OpenAI SİSTEM anahtarıdır (.env), setup'tan yazılmaz — client cache'i .env
    # değişiminde uygulama restart'ıyla tazelenir; burada invalidate gerekmez.
    if keys & {"IKAS_STORE_NAME", "IKAS_CLIENT_ID", "IKAS_CLIENT_SECRET"}:
        try:
            from Services import ikas_service
            ikas_service.invalidate(tenant)
        except Exception:
            pass

    # IG hesap kimliği değiştiyse hesap→tenant resolver cache'i eskimesin.
    if "IG_ACCOUNT_ID" in keys:
        try:
            from Services import tenant_service
            tenant_service.invalidate()
        except Exception:
            pass

    # Zorunlu creds değişmiş olabilir → kurulum-tamamlandı mandalı yeniden hesaplansın.
    reset_setup_cache(tenant)


# --------------------------------------------------------------------------
# Testler (self-contained; posted değer yoksa kayıtlıya düşer)
# --------------------------------------------------------------------------
def _resolve(values, key):
    """Test için değer: önce posted, yoksa .env/settings'teki mevcut değer."""
    v = values.get(key) if isinstance(values, dict) else None
    v = "" if v is None else str(v).strip()
    if v:
        return v
    meta = None
    for s in SECTIONS:
        if key in s["fields"]:
            meta = s["fields"][key]
            break
    if meta and meta["target"] == "setting":
        # Tekil okuma secret'ı ÇÖZER (get_all_stored_settings şifreli döndürür);
        # bağlantı testi ham/şifreli değeri token sanmasın diye get_stored_setting.
        from Services.settings_service import get_stored_setting
        return str(get_stored_setting(key) or "")
    return str(dotenv_values(ENV_PATH).get(key) or os.getenv(key) or "")


def _mark_tested(section_id):
    save_stored_settings({
        section_id.upper() + "_TESTED_AT": datetime.now().isoformat(timespec="seconds")
    })


def _send_whatsapp_raw(phone_number_id, token, to, body):
    url = f"https://graph.facebook.com/v23.0/{phone_number_id}/messages"
    return requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}},
        timeout=15,
    )


# --- Hata mesajı yardımcıları: kullanıcı dostu + secret sızdırmaz -----------
def _redact(text, secrets=()):
    """Metindeki API anahtarı/token değerlerini maskeler (gösterim/log güvenliği)."""
    s = str(text)
    for sec in secrets:
        sec = str(sec or "")
        if len(sec) >= 4:
            s = s.replace(sec, "***")
    return s


def _friendly_conn_error(exc):
    """Ağ istisnasını kullanıcı dostu mesaja çevirir. Ham istisna/secret basmaz."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "Zaman aşımı — sunucu yanıt vermedi. Bilgileri ve bağlantıyı kontrol edin."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "Bağlantı kurulamadı — adres/mağaza adını ve internet bağlantısını kontrol edin."
    return "Bağlantı sırasında beklenmeyen bir sorun oluştu. Lütfen tekrar deneyin."


def _http_error_message(r, secrets=()):
    """Sağlayıcı yanıtından güvenli, okunur bir hata mesajı üretir (secret redakte)."""
    msg = None
    try:
        body = r.json()
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
        msg = msg or body.get("error_description") or body.get("message")
    except Exception:
        msg = None
    return _redact(msg or f"HTTP {r.status_code}", secrets)


def _test_instagram(values):
    account_id = _resolve(values, "IG_ACCOUNT_ID")
    token = _resolve(values, "IG_ACCESS_TOKEN")
    if not account_id or not token:
        return {"ok": False, "error": "Instagram Hesap ID ve Access Token gerekli."}
    # API tabanı bağlantı yoluna göre değişir (graph.facebook.com / graph.instagram.com).
    # IG_API_BASE artık tenant ayarıdır; posted (henüz kaydedilmemiş) değeri de
    # dikkate al ki kullanıcı bağlantı türünü değiştirip kaydetmeden test edebilsin.
    base = _resolve(values, "IG_API_BASE") or "graph.facebook.com"
    ver = _resolve(values, "IG_GRAPH_VERSION") or "v23.0"
    # Alan adları tabana göre değişir; geçersiz alan 400 döndürmesin.
    fields = "username" if "instagram" in base else "id,name"
    try:
        # Token URL'ye değil Authorization başlığına konur — hata/loglarda sızmasın
        r = requests.get(
            f"https://{base}/{ver}/{account_id}",
            params={"fields": fields},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    except Exception as e:
        return {"ok": False, "error": _friendly_conn_error(e)}
    if r.status_code == 200:
        _mark_tested("instagram")
        label = ""
        try:
            data = r.json()
            label = data.get("username") or data.get("name") or ""
        except Exception:
            pass
        return {"ok": True, "message": "Instagram bağlantısı doğrulandı" + (f" (@{label})" if label else "") + "."}
    if r.status_code in (401, 403):
        return {"ok": False, "error": "Kimlik doğrulanamadı — Access Token geçersiz veya süresi dolmuş olabilir."}
    return {"ok": False, "error": "Doğrulanamadı: " + _http_error_message(r, [token])}


def _test_openai(values):
    key = _resolve(values, "OPENAI_API_KEY")
    if not key:
        return {"ok": False, "error": "OpenAI API anahtarı gerekli."}
    try:
        from openai import OpenAI
        OpenAI(api_key=key).models.list()
    except Exception as e:
        # Ham istisna basılmaz (anahtar sızabilir); tür bazlı dostu mesaj verilir
        name = e.__class__.__name__
        if "Authentication" in name or "Permission" in name:
            return {"ok": False, "error": "API anahtarı geçersiz — OpenAI kimlik doğrulaması başarısız."}
        if "RateLimit" in name:
            return {"ok": False, "error": "OpenAI hız sınırına takıldı; kısa süre sonra tekrar deneyin."}
        if "Connection" in name or "Timeout" in name:
            return {"ok": False, "error": "OpenAI'ye ulaşılamadı — internet bağlantısını kontrol edin."}
        return {"ok": False, "error": "API anahtarı doğrulanamadı. Lütfen kontrol edip tekrar deneyin."}
    _mark_tested("ai")
    return {"ok": True, "message": "OpenAI API anahtarı geçerli."}


def _test_ikas(values):
    store = _resolve(values, "IKAS_STORE_NAME")
    cid = _resolve(values, "IKAS_CLIENT_ID")
    secret = _resolve(values, "IKAS_CLIENT_SECRET")
    if not (store and cid and secret):
        return {"ok": False, "error": "Store Name, Client ID ve Client Secret gerekli."}
    try:
        # client_secret gövdede gönderilir (URL'de değil) — sızıntı riski yok
        r = requests.post(
            f"https://{store}.myikas.com/api/admin/oauth/token",
            data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except Exception as e:
        return {"ok": False, "error": _friendly_conn_error(e)}
    try:
        has_token = r.status_code == 200 and bool(r.json().get("access_token"))
    except Exception:
        has_token = False
    if has_token:
        _mark_tested("ikas")
        return {"ok": True, "message": "ikas kimlik doğrulaması başarılı."}
    if r.status_code in (400, 401, 403):
        return {"ok": False, "error": "ikas kimlik doğrulaması başarısız — Client ID/Secret veya Store Name hatalı olabilir."}
    return {"ok": False, "error": "ikas bağlantısı doğrulanamadı: " + _http_error_message(r, [cid, secret])}


def _test_product_search(values):
    query = (values.get("query") if isinstance(values, dict) else None) or "test"
    try:
        from Services.ikas_service import resolve_product_search
        resolve_product_search(str(query).strip())
    except Exception:
        # Ham istisna gösterilmez; ikas kimlik bilgileri dolaylı olarak sızmasın
        return {
            "ok": False,
            "error": "Ürün araması başarısız. Önce ikas bilgilerini kaydedip sunucuyu "
                     "yeniden başlatmayı deneyin.",
        }
    _mark_tested("product")
    return {"ok": True, "message": f"'{query}' için ürün araması çalıştı."}


def _test_notification(values):
    to = _resolve(values, "STORE_NOTIFY_PHONE")
    pid = _resolve(values, "WHATSAPP_PHONE_NUMBER_ID")
    token = _resolve(values, "WHATSAPP_ACCESS_TOKEN")
    if not to:
        return {"ok": False, "error": "Bildirim numarası gerekli."}
    if not (pid and token):
        return {"ok": False, "error": "Önce WhatsApp bilgilerini girin/kaydedin."}
    try:
        r = _send_whatsapp_raw(pid, token, to, "WhatsAgent kurulum testi ✅ — bildirimler bu numaraya gelecek.")
    except Exception as e:
        return {"ok": False, "error": _friendly_conn_error(e)}
    if r.status_code == 200:
        _mark_tested("notify")
        return {"ok": True, "message": "Test bildirimi gönderildi."}
    if r.status_code in (401, 403):
        return {"ok": False, "error": "Kimlik doğrulanamadı — WhatsApp Access Token geçersiz olabilir."}
    return {"ok": False, "error": "Gönderilemedi: " + _http_error_message(r, [token])}


# OpenAI SİSTEM anahtarıdır (.env); kurulumda ayrı bölüm/test yoktur. _test_openai
# fonksiyonu ileride operatör aracı olarak kullanılabilsin diye korunur.
_TESTS = {
    "instagram": _test_instagram,
    "ikas": _test_ikas,
    "product": _test_product_search,
    "notify": _test_notification,
}


def run_test(section_id, values):
    fn = _TESTS.get(section_id)
    if not fn:
        return {"ok": False, "error": "Bu bölüm için test yok."}
    return fn(values or {})


# --------------------------------------------------------------------------
# Kurulumu tamamla
# --------------------------------------------------------------------------
def mark_complete():
    env_vals = dotenv_values(ENV_PATH)
    stored = get_all_stored_settings()
    if not _db_ok():
        return {"ok": False, "error": "Veritabanına erişilemiyor."}
    missing = [k for k in REQUIRED_ENV_KEYS if not (env_vals.get(k) or os.getenv(k))]
    missing += [k for k in REQUIRED_SETTING_KEYS
                if not (stored.get(k) and str(stored.get(k)).strip())]
    if missing:
        return {"ok": False, "error": "Eksik zorunlu alanlar: " + ", ".join(missing)}
    ok = save_stored_settings({
        "SETUP_COMPLETED": "1",
        "SETUP_COMPLETED_AT": datetime.now().isoformat(timespec="seconds"),
    })
    if not ok:
        return {"ok": False, "error": "Durum kaydedilemedi (DB)."}
    _setup_complete_cache[_tenant_key()] = True
    return {"ok": True}
