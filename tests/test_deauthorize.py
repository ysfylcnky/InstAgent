"""A3 — Meta Deauthorize callback'i.

  * Geçerli signed_request → ilgili tenant pasifleşir (status=inactive),
    token temizlenir, resolver artık çözmez.
  * Geçersiz imza reddedilir (403), tenant etkilenmez.
  * Eşleşen tenant yoksa fail-safe 200 döner, hiçbir tenant değişmez.
"""

import pytest
from fastapi.testclient import TestClient

import config
import main
from Services.db import get_session, tenant_scope
from Services.models import Tenant
from Services import tenant_service, settings_service
from Services.meta_verify import build_signed_request
from conftest import TENANT_A, IG_ACCOUNT_A

APP_SECRET = "test-app-secret-deauth"


@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.setattr(config, "META_APP_SECRET", APP_SECRET)
    tenant_service.invalidate()
    return TestClient(main.app)


def _status(tenant_id):
    with get_session(scoped=False) as s:
        return s.get(Tenant, tenant_id).status


def _signed(user_id):
    return build_signed_request(
        {"user_id": user_id, "algorithm": "HMAC-SHA256"}, APP_SECRET
    )


def test_valid_deauthorize_deactivates_tenant(client):
    with tenant_scope(TENANT_A):
        settings_service.save_stored_settings({"IG_ACCESS_TOKEN": "SOME_TOKEN"})

    assert _status(TENANT_A) == "active"
    assert tenant_service.resolve_tenant_by_ig_account_id(IG_ACCOUNT_A) == TENANT_A

    r = client.post("/deauthorize", data={"signed_request": _signed(IG_ACCOUNT_A)})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    assert _status(TENANT_A) == "inactive"
    # Resolver artık bu hesabı çözmez (cache invalidate edildi + status inactive)
    assert tenant_service.resolve_tenant_by_ig_account_id(IG_ACCOUNT_A) is None
    # Bağlantı token'ı temizlendi
    with tenant_scope(TENANT_A):
        assert not settings_service.get_stored_setting("IG_ACCESS_TOKEN")


def test_invalid_signature_rejected(client):
    r = client.post("/deauthorize", data={"signed_request": "bogus.sig"})
    assert r.status_code == 403
    assert _status(TENANT_A) == "active"


def test_unknown_account_is_failsafe_ok(client):
    r = client.post("/deauthorize", data={"signed_request": _signed("does-not-exist")})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert _status(TENANT_A) == "active"
