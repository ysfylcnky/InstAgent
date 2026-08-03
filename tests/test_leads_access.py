"""Talepler (leads) sekmesi — YALNIZ platform operatörü erişimi.

  * Köken (default) tenant sahibi = platform operatörü → lead'leri görür,
    sayfa render olur, menüde "Talepler" öğesi çıkar.
  * Normal müşteri tenant'ı (id != default) → 403; sayfa dashboard'a yönlenir;
    menüde "Talepler" öğesi YOK.

TENANT_A = 1 = DEFAULT_TENANT_ID (operatör), TENANT_B = 2 (normal müşteri).
"""

import pytest
from fastapi.testclient import TestClient

import main
from Services import user_service
from conftest import TENANT_A, TENANT_B


@pytest.fixture()
def client(env, monkeypatch):
    user_service.create_user(TENANT_A, "operator@x.com", "parola12345")
    user_service.create_user(TENANT_B, "musteri@x.com", "parola12345")
    # Setup-gate testte kurulumu 'tamam' saysın (aksi halde /dashboard/* setup'a
    # yönlenir — gerçek MySQL olmadığı için). Leads sayfasının KENDİ gating'ini test ediyoruz.
    monkeypatch.setattr(main, "is_setup_complete", lambda *a, **k: True)
    return TestClient(main.app)


def _login(client, email):
    r = client.post("/login", data={"username": email, "password": "parola12345"},
                    follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    return client


def _seed_lead(client):
    r = client.post("/kayit", json={
        "store_name": "Test Butik", "contact_name": "Ayşe",
        "email": "ayse@test.com", "instagram": "@testbutik", "message": "Denemek istiyorum",
    })
    assert r.json() == {"ok": True}


def test_operator_sees_leads_api(client):
    _seed_lead(client)                       # public form (auth yok)
    _login(client, "operator@x.com")
    r = client.get("/admin/platform/signups")
    assert r.status_code == 200
    assert any(i["store_name"] == "Test Butik" for i in r.json()["items"])


def test_regular_tenant_forbidden_api(client):
    _login(client, "musteri@x.com")
    r = client.get("/admin/platform/signups")
    assert r.status_code == 403


def test_operator_page_renders(client):
    _login(client, "operator@x.com")
    r = client.get("/dashboard/leads")
    assert r.status_code == 200
    assert "Gelen Talepler" in r.text        # leads.html'e özgü


def test_regular_tenant_page_redirects(client):
    _login(client, "musteri@x.com")
    r = client.get("/dashboard/leads", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/dashboard"


def test_sidebar_item_only_for_operator(client):
    _login(client, "operator@x.com")
    assert "/dashboard/leads" in client.get("/dashboard").text
    _login(client, "musteri@x.com")
    assert "/dashboard/leads" not in client.get("/dashboard").text
