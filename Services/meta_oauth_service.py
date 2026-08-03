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

from sqlalchemy import select

from Services.db import get_session, tenant_scope
from Services.models import OAuthState, Tenant
from Services import settings_service, tenant_service
import config

STATE_TTL_SECONDS = 600  # 10 dk


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
    """Meta OAuth authorize URL'ini üretir (state ile). Platform config kullanır."""
    state = create_state(tenant_id, user_id)
    app_id = config.META_APP_ID
    redirect = redirect_uri or config.META_REDIRECT_URI or ""
    scope = ",".join(scopes or ["instagram_basic", "instagram_manage_messages", "pages_messaging"])
    # Not: gerçek uçta Meta'nın authorize endpoint'i kullanılır.
    return (
        f"https://www.facebook.com/{config.IG_GRAPH_VERSION}/dialog/oauth"
        f"?client_id={app_id}&redirect_uri={redirect}"
        f"&state={state}&scope={scope}&response_type=code"
    ), state


def _exchange_code_for_token(code, redirect_uri=None):
    """Yetki kodunu access token + IG Business Account ID ile değişir (Meta Graph).

    Gerçek uçta: code→token (app_secret ile), sonra token→bağlı IG hesap kimliği.
    Bu fonksiyon testlerde monkeypatch/enjekte edilir. Token loglanmaz.
    """
    raise OAuthError(
        "Token değişimi bu ortamda yapılandırılmadı (META_APP_SECRET / Graph API)."
    )


def handle_callback(state, code, exchange_fn=None, redirect_uri=None):
    """OAuth callback: state doğrula → token al → tenant'a ŞİFRELİ bağla.

    Döner: {tenant_id, ig_account_id}. Hata: OAuthError (fail-closed).
    """
    bound = consume_state(state)
    if bound is None:
        raise OAuthError("Geçersiz veya süresi dolmuş state (fail-closed).")

    tenant_id = bound["tenant_id"]

    exchange = exchange_fn or _exchange_code_for_token
    token, ig_account_id = exchange(code, redirect_uri)
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

    # Tenant ayarlarına yaz (token ŞİFRELİ — secret whitelist). Token loglanmaz.
    with tenant_scope(tenant_id):
        settings_service.save_stored_settings({
            "IG_ACCOUNT_ID": ig_account_id,
            "IG_ACCESS_TOKEN": token,
        })

    # Hesap→tenant resolver cache'i (yeni ig_account_id) + bu tenant'ın kurulum
    # mandalı (IG creds artık bağlı olabilir) eskimesin (Faz B3).
    tenant_service.invalidate()
    try:
        from Services import setup_service
        setup_service.reset_setup_cache(tenant_id)
    except Exception:
        pass

    return {"tenant_id": tenant_id, "ig_account_id": ig_account_id}
