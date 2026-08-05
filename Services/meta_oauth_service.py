"""Meta / Instagram bağlantısı — OAuth (Faz 9).

Tenant kendi Instagram Business hesabını sisteme bağlar. Platform seviyesi
(META_APP_ID/SECRET, redirect) sistem config'idir; tenant seviyesi (IG hesap
kimliği + access token) tenant_settings'e ŞİFRELİ yazılır.

Güvenlik:
  * OAuth `state`: tahmin edilemez (secrets), kısa ömürlü, TEK KULLANIMLIK ve
    tenant/user'a bağlı (oauth_states tablosu). Callback'te doğrulanıp silinir.
  * Callback başka tenant'ın bağlantısını EZEMEZ: state tenant'ı bağladığı için
    yazma yalnız o tenant'a olur; ayrıca hedef IG hesabı başka bir tenant'a
    bağlıysa reddedilir.
  * Token/secret ASLA loglanmaz.

Not: Gerçek token değişimi (`_exchange_code_for_token`) Meta Graph API'ye gider;
testlerde enjekte edilebilir (exchange_fn parametresi).
"""

import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
from sqlalchemy import select

from Services.db import get_session, tenant_scope
from Services.models import OAuthState, Tenant
from Services import settings_service, tenant_service
import config

STATE_TTL_SECONDS = 600  # 10 dk

# "Instagram API with Instagram Login" OAuth uçları (Facebook Sayfası yolu DEĞİL).
# Doğrulandı: developers.facebook.com/docs/instagram-platform (bkz. docs/meta-integration.md).
IG_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL = "https://api.instagram.com/oauth/access_token"       # code -> kısa ömürlü
IG_GRAPH_TOKEN_URL = "https://graph.instagram.com/access_token"     # kısa -> uzun (ig_exchange_token)
IG_GRAPH_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"  # uzun -> uzun (yenile)
IG_GRAPH_ME_URL = "https://graph.instagram.com/me"                  # hesap kimliği + kullanıcı adı

# Mesajlaşan bot için gereken izinler. (Eski business_* scope'ları 27 Oca 2025'te
# kaldırıldı; instagram_business_* güncel adlardır.)
IG_DEFAULT_SCOPES = ["instagram_business_basic", "instagram_business_manage_messages"]

# Instagram Login ile alınan token YALNIZ graph.instagram.com'a karşı geçerlidir;
# bağlanınca gönderim tabanı buna sabitlenir (aksi halde graph.facebook.com'a
# düşer ve mesaj gönderimi başarısız olur).
IG_LOGIN_API_BASE = "graph.instagram.com"


class OAuthError(Exception):
    """OAuth akışında güvenlik/doğrulama hatası (fail-closed)."""


def create_state(tenant_id, user_id=None, ttl=STATE_TTL_SECONDS):
    """Tenant/user'a bağlı, tahmin edilemez, kısa ömürlü tek-kullanımlık state üretir."""
    state = secrets.token_urlsafe(32)
    now = datetime.now()
    with get_session(scoped=False) as s:
        s.add(OAuthState(
            state=state, tenant_id=tenant_id, user_id=user_id,
            created_at=now, expires_at=now + timedelta(seconds=ttl),
        ))
    return state


def consume_state(state):
    """State'i doğrular ve TÜKETİR (siler). Geçerliyse {tenant_id, user_id}, değilse None.

    Geçerlilikten bağımsız olarak kayıt silinir (single-use); süresi dolmuşsa
    None döner. Bilinmeyen state → None (fail-closed).
    """
    if not state:
        return None
    now = datetime.now()
    with get_session(scoped=False) as s:
        row = s.execute(
            select(OAuthState).where(OAuthState.state == state)
        ).scalar_one_or_none()
        if row is None:
            return None
        bound = {"tenant_id": row.tenant_id, "user_id": row.user_id}
        expired = row.expires_at < now
        s.delete(row)  # tek kullanımlık: her hâlükârda tüket

    if expired:
        return None
    return bound


def build_authorize_url(tenant_id, user_id=None, redirect_uri=None, scopes=None):
    """Instagram Business Login authorize URL'ini üretir (state ile).

    Instagram App kimliği + izinli redirect URI SİSTEM config'inden gelir
    (IG_APP_ID/IG_REDIRECT_URI → yoksa META_APP_*). Kullanıcı burada mağazasını
    yetkilendirir; geri dönüşte `?code` gelir (bkz. handle_callback).
    """
    state = create_state(tenant_id, user_id)
    app_id = config.IG_APP_ID or ""
    redirect = redirect_uri or config.IG_REDIRECT_URI or ""
    scope = ",".join(scopes or IG_DEFAULT_SCOPES)
    return (
        f"{IG_AUTHORIZE_URL}"
        f"?client_id={quote(str(app_id))}"
        f"&redirect_uri={quote(redirect)}"
        f"&response_type=code"
        f"&scope={quote(scope)}"
        f"&state={state}"
    ), state


# --------------------------------------------------------------------------
# İzole Graph çağrıları (`_graph_*` / `_ig_*`) — testte tek tek monkeypatch'lenir.
# Hiçbiri token/secret loglamaz. Hata → OAuthError (kullanıcı dostu, sır sızmaz).
# --------------------------------------------------------------------------
def _graph_error(resp, fallback):
    """Graph yanıtından güvenli, okunur bir hata mesajı çıkarır (secret basmaz)."""
    try:
        err = resp.json().get("error_message") or resp.json().get("error", {})
        if isinstance(err, dict):
            err = err.get("message")
        return str(err or fallback)
    except Exception:
        return fallback


def _ig_exchange_code_for_short_token(code, redirect_uri):
    """authorization_code → kısa ömürlü Instagram user token. (app secret gövdede)."""
    try:
        r = requests.post(
            IG_TOKEN_URL,
            data={
                "client_id": config.IG_APP_ID,
                "client_secret": config.IG_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri or config.IG_REDIRECT_URI or "",
                "code": code,
            },
            timeout=15,
        )
    except requests.RequestException:
        raise OAuthError("Instagram'a ulaşılamadı (token değişimi).")
    if r.status_code != 200:
        raise OAuthError("Yetki kodu doğrulanamadı: " + _graph_error(r, "geçersiz kod veya redirect."))
    body = r.json()
    token = body.get("access_token")
    if not token:
        raise OAuthError("Instagram kısa ömürlü token döndürmedi.")
    return token


def _ig_exchange_long_lived_token(short_token):
    """Kısa ömürlü token → ~60 gün geçerli uzun ömürlü token (ig_exchange_token)."""
    try:
        r = requests.get(
            IG_GRAPH_TOKEN_URL,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": config.IG_APP_SECRET,
                "access_token": short_token,
            },
            timeout=15,
        )
    except requests.RequestException:
        raise OAuthError("Instagram'a ulaşılamadı (uzun ömürlü token).")
    if r.status_code != 200:
        raise OAuthError("Uzun ömürlü token alınamadı: " + _graph_error(r, "değişim reddedildi."))
    token = r.json().get("access_token")
    if not token:
        raise OAuthError("Instagram uzun ömürlü token döndürmedi.")
    return token


def _ig_fetch_account(long_token):
    """Bağlı profesyonel hesabın (user_id) ve kullanıcı adını döndürür.

    `user_id` = Instagram profesyonel hesap kimliği; webhook `entry.id` /
    mesaj `recipient.id` ile AYNIDIR (routing anahtarı). `id` app-scoped'tır ve
    routing için YANLIŞTIR — bu yüzden `user_id` kullanılır.
    """
    try:
        r = requests.get(
            IG_GRAPH_ME_URL,
            params={"fields": "user_id,username", "access_token": long_token},
            timeout=15,
        )
    except requests.RequestException:
        raise OAuthError("Instagram'a ulaşılamadı (hesap bilgisi).")
    if r.status_code != 200:
        raise OAuthError("Hesap bilgisi alınamadı: " + _graph_error(r, "profil okunamadı."))
    body = r.json()
    ig_account_id = body.get("user_id")
    if not ig_account_id:
        raise OAuthError("Instagram hesap kimliği (user_id) alınamadı.")
    return str(ig_account_id), (body.get("username") or None)


def _ig_refresh_long_lived_token(long_token):
    """Uzun ömürlü token'ı ~60 gün daha uzatır (ig_refresh_token)."""
    try:
        r = requests.get(
            IG_GRAPH_REFRESH_URL,
            params={"grant_type": "ig_refresh_token", "access_token": long_token},
            timeout=15,
        )
    except requests.RequestException:
        raise OAuthError("Instagram'a ulaşılamadı (token yenileme).")
    if r.status_code != 200:
        raise OAuthError("Token yenilenemedi: " + _graph_error(r, "yenileme reddedildi."))
    token = r.json().get("access_token")
    if not token:
        raise OAuthError("Instagram yenilenmiş token döndürmedi.")
    return token


def _exchange_code_for_token(code, redirect_uri=None):
    """Yetki kodunu uzun ömürlü token + IG hesap kimliği + kullanıcı adı ile değişir.

    Instagram Business Login akışı: code → kısa token → uzun token → /me. Dönüş
    3'lü (token, ig_account_id, username). İzole `_ig_*` adımları testte
    monkeypatch'lenir; bu yüzden burada gerçek ağ çağrısı test edilmeden çalışmaz.
    Token/secret ASLA loglanmaz.
    """
    short_token = _ig_exchange_code_for_short_token(code, redirect_uri)
    long_token = _ig_exchange_long_lived_token(short_token)
    ig_account_id, username = _ig_fetch_account(long_token)
    return long_token, ig_account_id, username


def handle_callback(state, code, exchange_fn=None, redirect_uri=None):
    """OAuth callback: state doğrula → token al → tenant'a ŞİFRELİ bağla.

    Döner: {tenant_id, ig_account_id, username}. Hata: OAuthError (fail-closed).
    """
    bound = consume_state(state)
    if bound is None:
        raise OAuthError("Geçersiz veya süresi dolmuş state (fail-closed).")

    tenant_id = bound["tenant_id"]

    exchange = exchange_fn or _exchange_code_for_token
    # Dönüş 2'li (token, id) VEYA 3'lü (token, id, username) olabilir — enjekte
    # edilen test exchange_fn'leri 2'li döndürür; gerçek akış username de verir.
    result = exchange(code, redirect_uri)
    token, ig_account_id = result[0], result[1]
    username = result[2] if len(result) > 2 else None
    ig_account_id = str(ig_account_id)

    # Hedef IG hesabı BAŞKA bir tenant'a bağlıysa reddet (cross-tenant overwrite yok).
    with get_session(scoped=False) as s:
        other = s.execute(
            select(Tenant).where(
                Tenant.ig_account_id == ig_account_id, Tenant.id != tenant_id
            )
        ).scalar_one_or_none()
        if other is not None:
            raise OAuthError("Bu Instagram hesabı zaten başka bir tenant'a bağlı.")

        tenant = s.get(Tenant, tenant_id)
        if tenant is None:
            raise OAuthError("Tenant bulunamadı.")
        tenant.ig_account_id = ig_account_id

        # Deauthorize sonrası YENİDEN BAĞLANMA: /deauthorize tenant'ı
        # status="inactive" yapar (bkz. gdpr_service.deauthorize_tenant) ve
        # routing yalnız "active" tenant'ları çözer (tenant_service). Burada geri
        # açmazsak müşteri panelde "bağlandı" görür ama webhook'lar fail-closed
        # reddedilir ve bot sessiz kalır.
        #
        # Yalnız "inactive" → "active" geçişi yapılır. Operatörün başka bir
        # gerekçeyle (ör. ödeme/askıya alma) koyduğu bir durumu Instagram'ı
        # yeniden bağlayarak kendi kendine açmak MÜMKÜN OLMAMALI; bu yüzden
        # koşul dar tutulur.
        if tenant.status == "inactive":
            tenant.status = "active"

    # Tenant ayarlarına yaz (token ŞİFRELİ — secret whitelist). Token loglanmaz.
    # IG_API_BASE de graph.instagram.com'a sabitlenir: Instagram Login token'ı
    # yalnız bu tabana karşı geçerlidir (gönderim aksi halde başarısız olur).
    writes = {
        "IG_ACCOUNT_ID": ig_account_id,
        "IG_ACCESS_TOKEN": token,
        "IG_API_BASE": IG_LOGIN_API_BASE,
    }
    if username:
        writes["IG_USERNAME"] = username
    with tenant_scope(tenant_id):
        settings_service.save_stored_settings(writes)

    # Hesap→tenant resolver cache'i (yeni ig_account_id) + bu tenant'ın kurulum
    # mandalı (IG creds artık bağlı olabilir) eskimesin (Faz B3).
    tenant_service.invalidate()
    try:
        from Services import setup_service
        setup_service.reset_setup_cache(tenant_id)
    except Exception:
        pass

    return {"tenant_id": tenant_id, "ig_account_id": ig_account_id, "username": username}


def refresh_token(tenant_id, refresh_fn=None):
    """Tenant'ın uzun ömürlü Instagram token'ını yeniler (~60 gün uzatır).

    "60 günde bir elle güncelle" derdini çözer: mevcut şifreli token çözülür,
    Graph'tan yenisi alınır ve tekrar şifreli yazılır. Yalnız Instagram Login
    (graph.instagram.com) token'ları yenilenebilir. `refresh_fn` testte enjekte
    edilir. Başarı → {"ok": True}; hata → OAuthError (fail-closed, sır sızmaz).
    """
    refresh = refresh_fn or _ig_refresh_long_lived_token
    with tenant_scope(tenant_id):
        current = settings_service.get_stored_setting("IG_ACCESS_TOKEN")
        if not current:
            raise OAuthError("Yenilenecek Instagram token'ı yok — önce hesabı bağlayın.")
        new_token = refresh(current)
        settings_service.save_stored_settings({"IG_ACCESS_TOKEN": new_token})
    return {"ok": True}
