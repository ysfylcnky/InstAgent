"""Panel kimlik doğrulaması — tenant-aware JWT oturum yönetimi (Faz 2).

Auth artık email + parola tabanlıdır ve çok-kiracılıdır:
  * Kullanıcılar DB'deki `users` tablosunda tutulur (user_service).
  * Başarılı login sonrası JWT payload'ı `{user_id, tenant_id, role, sub=email}`
    taşır. **Tenant kimliği yalnızca bu auth context'inden çözülür** — request
    parametresi/header/query'den gelen tenant değerine ASLA güvenilmez.
  * Parola düz metin karşılaştırılmaz; bcrypt hash doğrulanır.

Geriye dönük uyum:
  Henüz DB kullanıcısı yoksa (tek-tenant Mumi kurulumu), .env'deki
  DASHBOARD_USER + DASHBOARD_PASSWORD_HASH ile giriş, DEFAULT_TENANT_ID (1)
  altında kabul edilir. Böylece mevcut panel girişi kesintiye uğramaz.
"""

import time

import bcrypt
import jwt

from config import (
    JWT_SECRET,
    JWT_EXPIRE_HOURS,
    JWT_ALGORITHM,
    DASHBOARD_USER,
    DASHBOARD_PASSWORD,
    DASHBOARD_PASSWORD_HASH,
)
from Services.models import DEFAULT_TENANT_ID

# Çerez adı tek noktada tanımlıdır (DRY); set/clear/read hepsi bunu kullanır.
COOKIE_NAME = "wa_session"

# bcrypt tek seferde en fazla 72 bayt işler; daha uzun parolalar sessizce
# kırpılır. 72 bayt sınırı korunur ve doğrulama simetrik kalır.
_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(password):
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password):
    """Düz metin paroladan bcrypt hash üretir."""
    return bcrypt.hashpw(_to_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, stored_hash):
    """Parolayı bcrypt hash'iyle doğrular. stored_hash None ise sahte kontrol + False.

    stored_hash None olsa bile bcrypt çağrısı yapılır; "kullanıcı/hash var mı"
    bilgisi yanıt süresinden sızmasın (timing attack koruması).
    """
    candidate = _to_bcrypt_bytes(password or "")
    if not stored_hash:
        bcrypt.checkpw(candidate, bcrypt.hashpw(b"x", bcrypt.gensalt()))
        return False
    if isinstance(stored_hash, str):
        stored_hash = stored_hash.encode("utf-8")
    try:
        return bcrypt.checkpw(candidate, stored_hash)
    except ValueError:
        return False


# ----------------------------------------------------------------------
# Legacy (.env tek kullanıcı) fallback
# ----------------------------------------------------------------------

def _resolve_legacy_hash():
    """Legacy tek-kullanıcı bcrypt hash'i; yoksa None."""
    if DASHBOARD_PASSWORD_HASH:
        return DASHBOARD_PASSWORD_HASH.encode("utf-8")
    if DASHBOARD_PASSWORD:
        print(
            "⚠️ DASHBOARD_PASSWORD_HASH tanımlı değil — düz metin "
            "DASHBOARD_PASSWORD'den geçici hash türetildi. generate_password_hash.py "
            "ile hash üretip .env'e DASHBOARD_PASSWORD_HASH koyun."
        )
        return bcrypt.hashpw(_to_bcrypt_bytes(DASHBOARD_PASSWORD), bcrypt.gensalt())
    return None


_LEGACY_HASH = _resolve_legacy_hash()


def _legacy_authenticate(identifier, password):
    """Legacy .env kullanıcısını doğrular → auth ctx (tenant 1) ya da None."""
    if _LEGACY_HASH is None:
        return None
    import hmac

    user_ok = hmac.compare_digest(identifier or "", DASHBOARD_USER or "")
    pass_ok = verify_password(password, _LEGACY_HASH)
    if user_ok and pass_ok:
        return {
            "user_id": None,
            "tenant_id": DEFAULT_TENANT_ID,
            "email": DASHBOARD_USER,
            "role": "owner",
        }
    return None


# ----------------------------------------------------------------------
# Kimlik doğrulama girişi
# ----------------------------------------------------------------------

def authenticate(identifier, password):
    """Email+parola (DB) ya da legacy kullanıcı → auth context dict / None.

    Önce DB kullanıcısı denenir; bulunamazsa legacy .env kullanıcısına düşülür.
    """
    # 1) DB kullanıcısı (email)
    try:
        from Services.user_service import authenticate_db_user

        ctx = authenticate_db_user(identifier, password)
        if ctx is not None:
            return ctx
    except Exception as e:
        # DB erişilemezse login tamamen kilitlenmesin: legacy'ye düş.
        print("🔴 DB auth hatası, legacy'ye düşülüyor:", e)

    # 2) Legacy .env tek kullanıcı
    return _legacy_authenticate(identifier, password)


# Geriye dönük uyum: eski imza (username, password) -> bool.
def verify_credentials(username, password):
    return authenticate(username, password) is not None


# ----------------------------------------------------------------------
# Token üretimi / doğrulaması
# ----------------------------------------------------------------------

def create_token(ctx):
    """Auth context'inden imzalı, süreli JWT üretir.

    ctx: {user_id, tenant_id, email, role}. Tenant kimliği token'a gömülür;
    sonraki isteklerde tenant BUNDAN çözülür.
    """
    now = int(time.time())
    payload = {
        "sub": ctx.get("email"),
        "uid": ctx.get("user_id"),
        "tid": ctx.get("tenant_id"),
        "role": ctx.get("role", "owner"),
        "iat": now,
        "exp": now + JWT_EXPIRE_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token):
    """Token'ı doğrular; geçerliyse auth context dict, değilse None.

    Dönen dict: {user_id, tenant_id, email, role}. tenant_id yoksa (bozuk/eski
    token) None döner — fail-closed.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

    tid = payload.get("tid")
    if tid is None:
        return None

    return {
        "user_id": payload.get("uid"),
        "tenant_id": tid,
        "email": payload.get("sub"),
        "role": payload.get("role", "owner"),
    }


def is_auth_configured():
    """Panel girişi yapılandırılmış mı (legacy hash ya da DB kullanıcısı + JWT secret)."""
    if not JWT_SECRET:
        return False
    if _LEGACY_HASH is not None:
        return True
    try:
        from sqlalchemy import select
        from Services.db import get_session
        from Services.models import User

        with get_session(scoped=False) as s:
            return s.execute(select(User).limit(1)).first() is not None
    except Exception:
        return False
