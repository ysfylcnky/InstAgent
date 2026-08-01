"""Faz 9 — Meta OAuth state güvenliği + tenant bağlama kanıtları.

  * state tahmin edilemez ve yeterince uzun.
  * TEK KULLANIMLIK: ikinci kez tüketilemez.
  * Süresi dolan state reddedilir.
  * Bilinmeyen/boş state reddedilir.
  * Callback token'ı DOĞRU tenant'a şifreli yazar.
  * Callback başka tenant'a bağlı IG hesabını EZEMEZ.
"""

import pytest

from Services.db import get_session, tenant_scope
from Services.models import OAuthState, Tenant
from Services import meta_oauth_service as oauth
from Services import settings_service
from conftest import TENANT_A, TENANT_B


def test_state_is_unguessable_and_bound(env):
    state = oauth.create_state(TENANT_A, user_id=7)
    assert len(state) >= 32
    bound = oauth.consume_state(state)
    assert bound == {"tenant_id": TENANT_A, "user_id": 7}


def test_state_is_single_use(env):
    state = oauth.create_state(TENANT_A)
    assert oauth.consume_state(state) is not None
    # İkinci kez → None (tüketildi)
    assert oauth.consume_state(state) is None


def test_expired_state_rejected(env):
    state = oauth.create_state(TENANT_A, ttl=-1)  # zaten süresi dolmuş
    assert oauth.consume_state(state) is None


def test_unknown_state_rejected(env):
    assert oauth.consume_state("does-not-exist") is None
    assert oauth.consume_state("") is None
    assert oauth.consume_state(None) is None


def test_callback_binds_token_to_correct_tenant_encrypted(env):
    state = oauth.create_state(TENANT_A, user_id=1)

    # Token değişimini enjekte et (gerçek Meta çağrısı yok)
    def fake_exchange(code, redirect_uri=None):
        return ("SECRET_IG_TOKEN_A", "17811111111111111")

    res = oauth.handle_callback(state, "auth_code", exchange_fn=fake_exchange)
    assert res["tenant_id"] == TENANT_A
    assert res["ig_account_id"] == "17811111111111111"

    # Token A tenant'ına ŞİFRELİ yazıldı ve doğru çözülüyor
    with tenant_scope(TENANT_A):
        assert settings_service.get_stored_setting("IG_ACCESS_TOKEN") == "SECRET_IG_TOKEN_A"

    # tenant.ig_account_id güncellendi
    with get_session(scoped=False) as s:
        t = s.get(Tenant, TENANT_A)
        assert t.ig_account_id == "17811111111111111"


def test_callback_cannot_overwrite_other_tenant_connection(env):
    # B zaten "17800000000000002" hesabına bağlı (conftest). A callback'i onu ezemez.
    state = oauth.create_state(TENANT_A)

    def steal_exchange(code, redirect_uri=None):
        return ("ATTACKER_TOKEN", "17800000000000002")  # B'nin hesabı

    with pytest.raises(oauth.OAuthError):
        oauth.handle_callback(state, "code", exchange_fn=steal_exchange)

    # B'nin bağlantısı bozulmadı
    with get_session(scoped=False) as s:
        b = s.get(Tenant, TENANT_B)
        assert b.ig_account_id == "17800000000000002"
