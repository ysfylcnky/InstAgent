"""Instagram Business Login (tek-tıkla bağlan) — Graph çağrıları MOCK'lanarak
code→token→settings akışının doğrulanması.

whatsAgent'taki debug_faz6c_embedded_signup.py'nin InstaAgent karşılığıdır:
gerçek ağ yok; izole `_ig_*` adımları monkeypatch'lenir. Kanıtlar:
  * authorize URL Instagram Login ucunu ve güncel scope'ları kullanır.
  * _exchange_code_for_token 3'lü (uzun token, hesap ID, kullanıcı adı) döndürür.
  * handle_callback tenant'ın ŞİFRELİ settings'ine yazar; hesap ID = /me user_id
    (app-scoped id DEĞİL); IG_API_BASE graph.instagram.com'a sabitlenir.
  * Graph hatası fail-closed: OAuthError + settings'e YAZMAZ.
  * refresh_token uzun ömürlü token'ı yeniler (yerine yenisini yazar).
"""

import pytest

from Services.db import get_session, tenant_scope
from Services.models import Tenant
from Services import meta_oauth_service as oauth
from Services import settings_service
import config
from conftest import TENANT_A


# /me'nin döndürdüğü Instagram profesyonel hesap kimliği (webhook entry.id ile
# aynı). Kısa token'ın app-scoped id'siyle KARIŞTIRILMAMALI.
ME_IG_ACCOUNT_ID = "17841400000000042"
ME_USERNAME = "mumifashion"


def _patch_graph(monkeypatch, *, short="SHORT_TOK", long="LONG_TOK_60D",
                 account=ME_IG_ACCOUNT_ID, username=ME_USERNAME):
    """Üç izole Graph adımını mock'lar (gerçek ağ yok)."""
    monkeypatch.setattr(oauth, "_ig_exchange_code_for_short_token",
                        lambda code, redirect_uri: short)
    monkeypatch.setattr(oauth, "_ig_exchange_long_lived_token",
                        lambda short_token: long)
    monkeypatch.setattr(oauth, "_ig_fetch_account",
                        lambda long_token: (account, username))


def test_authorize_url_uses_instagram_login_endpoint_and_scopes(env, monkeypatch):
    monkeypatch.setattr(config, "IG_APP_ID", "111222333")
    monkeypatch.setattr(config, "IG_REDIRECT_URI", "https://app.example/connect/instagram/callback")

    url, state = oauth.build_authorize_url(TENANT_A, user_id=5)

    assert url.startswith("https://www.instagram.com/oauth/authorize")
    assert "client_id=111222333" in url
    assert "response_type=code" in url
    assert "instagram_business_basic" in url
    assert "instagram_business_manage_messages" in url
    # FB Sayfası yolunun izleri OLMAMALI
    assert "facebook.com" not in url
    assert "pages_messaging" not in url
    assert f"state={state}" in url


def test_exchange_returns_long_token_account_and_username(env, monkeypatch):
    _patch_graph(monkeypatch)
    token, account, username = oauth._exchange_code_for_token("auth_code")
    assert token == "LONG_TOK_60D"          # kısa DEĞİL, uzun ömürlü olan
    assert account == ME_IG_ACCOUNT_ID      # /me user_id
    assert username == ME_USERNAME


def test_callback_writes_token_account_username_and_base_encrypted(env, monkeypatch):
    _patch_graph(monkeypatch)
    state = oauth.create_state(TENANT_A, user_id=1)

    res = oauth.handle_callback(state, "auth_code")  # gerçek orchestration çalışır
    assert res["ig_account_id"] == ME_IG_ACCOUNT_ID
    assert res["username"] == ME_USERNAME

    with tenant_scope(TENANT_A):
        # Token ŞİFRELİ yazıldı ve doğru çözülüyor (tekil okuma çözer)
        assert settings_service.get_stored_setting("IG_ACCESS_TOKEN") == "LONG_TOK_60D"
        assert settings_service.get_stored_setting("IG_ACCOUNT_ID") == ME_IG_ACCOUNT_ID
        assert settings_service.get_stored_setting("IG_USERNAME") == ME_USERNAME
        # Instagram Login token'ı yalnız graph.instagram.com'a geçerli → sabitlenir
        assert settings_service.get_stored_setting("IG_API_BASE") == "graph.instagram.com"

    # Webhook routing anahtarı (Tenant.ig_account_id) /me user_id ile güncellendi
    with get_session(scoped=False) as s:
        assert s.get(Tenant, TENANT_A).ig_account_id == ME_IG_ACCOUNT_ID


def test_callback_failclosed_on_graph_error_writes_nothing(env, monkeypatch):
    def boom(code, redirect_uri):
        raise oauth.OAuthError("Yetki kodu doğrulanamadı.")
    monkeypatch.setattr(oauth, "_ig_exchange_code_for_short_token", boom)

    state = oauth.create_state(TENANT_A)
    with pytest.raises(oauth.OAuthError):
        oauth.handle_callback(state, "bad_code")

    # Hiçbir kredensiyel yazılmadı (fail-closed)
    with tenant_scope(TENANT_A):
        assert settings_service.get_stored_setting("IG_ACCESS_TOKEN") is None


def test_refresh_token_swaps_stored_token(env, monkeypatch):
    # Önce bir token bağlıymış gibi yaz (şifreli)
    with tenant_scope(TENANT_A):
        settings_service.save_stored_settings({"IG_ACCESS_TOKEN": "OLD_LONG_TOKEN"})

    monkeypatch.setattr(oauth, "_ig_refresh_long_lived_token",
                        lambda current: "NEW_LONG_TOKEN")
    res = oauth.refresh_token(TENANT_A)
    assert res["ok"] is True

    with tenant_scope(TENANT_A):
        assert settings_service.get_stored_setting("IG_ACCESS_TOKEN") == "NEW_LONG_TOKEN"


def test_refresh_without_existing_token_raises(env):
    with pytest.raises(oauth.OAuthError):
        oauth.refresh_token(TENANT_A)
