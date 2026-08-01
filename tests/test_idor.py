"""Faz 7 — Dashboard/API tenant izolasyonu (IDOR) kanıtları.

Tenant kimliği auth context'inden (JWT) çözülür; frontend'in gönderdiği
resource ID'sine güvenilmez. A kullanıcısı B'nin resource ID'siyle endpoint
çağırsa bile veri ALAMAZ.
"""

import pytest
from fastapi.testclient import TestClient

import main
from Services import user_service, dashboard_service
from conftest import (
    TENANT_A, TENANT_B,
    seed_conversation, seed_customer, seed_order,
)


@pytest.fixture()
def app_client(env, monkeypatch):
    # Kur (currency network çağrısını kes)
    monkeypatch.setattr(dashboard_service, "get_usd_try_rate", lambda: 40.0)

    # İki tenant'a kullanıcı + veri
    user_service.create_user(TENANT_A, "a@a.com", "parola12345")
    user_service.create_user(TENANT_B, "b@b.com", "parola12345")

    seed_conversation(TENANT_A, "cust_a", "A gizli mesajı")
    seed_customer(TENANT_A, "cust_a", "Ali (A)")
    seed_order(TENANT_A, "cust_a", "Abaya")

    seed_conversation(TENANT_B, "cust_b", "B gizli mesajı")
    seed_customer(TENANT_B, "cust_b", "Veli (B)")
    seed_order(TENANT_B, "cust_b", "Trençkot")

    return TestClient(main.app)


def _login(client, email):
    r = client.post("/login", data={"username": email, "password": "parola12345"},
                    follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    # httpx TestClient çerezi saklar; sonraki isteklerde otomatik gönderilir.
    return client


def test_conversations_list_isolated(app_client):
    _login(app_client, "a@a.com")
    data = app_client.get("/admin/conversations").json()
    senders = {item["sender"] for item in data["items"]}
    assert senders == {"cust_a"}
    assert "cust_b" not in senders


def test_idor_conversation_detail_cross_tenant_blocked(app_client):
    _login(app_client, "a@a.com")
    # A, B'nin sender ID'siyle konuşma detayını ister → veri ALAMAZ
    data = app_client.get("/admin/conversations/detail", params={"sender": "cust_b"}).json()
    assert data["messages"] == []
    contents = [m["content"] for m in data["messages"]]
    assert "B gizli mesajı" not in contents


def test_idor_customer_detail_cross_tenant_blocked(app_client):
    _login(app_client, "a@a.com")
    # A, B'nin müşteri ID'siyle (phone/IGSID) müşteri detayını ister
    data = app_client.get("/admin/customers/detail", params={"phone": "cust_b"}).json()
    assert data["ad_soyad"] is None      # B'nin müşterisi görünmez
    assert data["orders"] == []          # B'nin siparişleri görünmez


def test_customers_list_isolated(app_client):
    _login(app_client, "b@b.com")
    data = app_client.get("/admin/customers").json()
    phones = {item["phone"] for item in data["items"]}
    assert phones == {"cust_b"}


def test_unauthenticated_blocked(app_client):
    # Oturumsuz istek → JSON 401 (admin) ya da login yönlendirmesi
    r = app_client.get("/admin/conversations", follow_redirects=False)
    assert r.status_code in (401, 307)


def test_tenant_from_token_not_query(app_client):
    # A giriş yapar; endpoint'e ekstra 'tenant_id' query'si vermek işe yaramaz
    _login(app_client, "a@a.com")
    data = app_client.get("/admin/conversations", params={"tenant_id": TENANT_B}).json()
    senders = {item["sender"] for item in data["items"]}
    assert senders == {"cust_a"}  # query'deki tenant_id yok sayılır
