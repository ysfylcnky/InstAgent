"""A2 — Meta Veri Silme (Data Deletion) callback'i.

  * Geçerli signed_request ile ilgili IGSID'in verisi tüm tablolardan silinir.
  * Başka kullanıcı / başka tenant verisi etkilenmez.
  * Geçersiz / eksik imza reddedilir (403).
"""

import pytest
from fastapi.testclient import TestClient

import config
import main
from Services.db import get_session, tenant_scope
from Services.models import Conversation, Customer, Order, UsageLog
from Services.meta_verify import build_signed_request
from conftest import (
    TENANT_A, TENANT_B,
    seed_conversation, seed_customer, seed_order, seed_usage,
)

APP_SECRET = "test-app-secret-del"


@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.setattr(config, "META_APP_SECRET", APP_SECRET)
    return TestClient(main.app)


def _counts(tenant_id, igsid):
    with tenant_scope(tenant_id):
        with get_session() as s:
            return {
                "conversations": s.query(Conversation).filter(Conversation.sender == igsid).count(),
                "orders": s.query(Order).filter(Order.customer_phone == igsid).count(),
                "customers": s.query(Customer).filter(Customer.phone == igsid).count(),
                "usage_logs": s.query(UsageLog).filter(UsageLog.sender == igsid).count(),
            }


def _seed(tenant_id, igsid):
    seed_conversation(tenant_id, igsid, "merhaba")
    seed_customer(tenant_id, igsid, "Ali Veli")
    seed_order(tenant_id, igsid, "ayakkabi")
    seed_usage(tenant_id, igsid)


def _signed(user_id):
    return build_signed_request(
        {"user_id": user_id, "algorithm": "HMAC-SHA256"}, APP_SECRET
    )


def test_valid_deletion_removes_user_data(client):
    _seed(TENANT_A, "user_del_1")
    _seed(TENANT_B, "user_other")  # başka kullanıcı + başka tenant — dokunulmamalı

    r = client.post("/data-deletion", data={"signed_request": _signed("user_del_1")})
    assert r.status_code == 200
    body = r.json()
    assert body.get("url") and body.get("confirmation_code")

    # Silinen kullanıcının A'daki verisi tamamen gitti
    assert _counts(TENANT_A, "user_del_1") == {
        "conversations": 0, "orders": 0, "customers": 0, "usage_logs": 0
    }
    # Başka tenant'taki başka kullanıcı olduğu gibi duruyor
    assert _counts(TENANT_B, "user_other") == {
        "conversations": 1, "orders": 1, "customers": 1, "usage_logs": 1
    }


def test_same_igsid_deleted_across_all_tenants(client):
    # Aynı IGSID iki mağazaya da mesaj atmışsa silme talebi HER YERDEN kaldırır.
    _seed(TENANT_A, "shared_user")
    _seed(TENANT_B, "shared_user")

    r = client.post("/data-deletion", data={"signed_request": _signed("shared_user")})
    assert r.status_code == 200

    assert _counts(TENANT_A, "shared_user")["conversations"] == 0
    assert _counts(TENANT_B, "shared_user")["conversations"] == 0


def test_invalid_signature_rejected(client):
    _seed(TENANT_A, "user_keep")
    r = client.post("/data-deletion", data={"signed_request": "bogus.payload"})
    assert r.status_code == 403
    # Veri silinmedi
    assert _counts(TENANT_A, "user_keep")["conversations"] == 1


def test_missing_signed_request_rejected(client):
    r = client.post("/data-deletion", data={})
    assert r.status_code == 403
