"""Faz 4 — Instagram webhook tenant routing kanıtları (uçtan uca).

  * A'nın hesabına gelen webhook A tenant'ında işlenir (veriler tenant_id=A).
  * B'nin hesabına gelen webhook B tenant'ında işlenir (tenant_id=B).
  * Bilinmeyen hesap HİÇBİR tenant'a gitmez (fail-closed, veri yazılmaz).
  * A ve B aynı anda gelse bile veriler çapraz görünmez.

Ağ çağrıları (Instagram gönderimi, OpenAI) monkeypatch ile devre dışı; sadece
routing + tenant izolasyonu doğrulanır.
"""

import pytest
from fastapi.testclient import TestClient

import main
from Services.db import get_session, tenant_scope
from Services.models import Conversation
from Services import tenant_service
from conftest import TENANT_A, TENANT_B, IG_ACCOUNT_A, IG_ACCOUNT_B


@pytest.fixture()
def client(env, monkeypatch):
    # Ağ yan etkilerini kes: müşteriye gönderim ve OpenAI çağrısı.
    monkeypatch.setattr(main, "send_instagram_message", lambda rid, msg: None)
    monkeypatch.setattr(
        main, "general_chat",
        lambda prompt, text, sender: {"answer": "yardımcı olabilirim", "tool_call": None},
    )
    # tenant_service DB cache'i env DB'siyle uyumlu olsun
    tenant_service.invalidate()
    return TestClient(main.app)


def _ig_text_event(account_id, sender_igsid, text, mid):
    return {
        "object": "instagram",
        "entry": [{
            "id": account_id,
            "time": 1,
            "messaging": [{
                "sender": {"id": sender_igsid},
                "recipient": {"id": account_id},
                "timestamp": 1,
                "message": {"mid": mid, "text": text},
            }],
        }],
    }


def _conv_count(tenant_id):
    with tenant_scope(tenant_id):
        with get_session() as s:
            return s.query(Conversation).count()


def test_webhook_routes_to_tenant_a(client):
    r = client.post("/webhook", json=_ig_text_event(IG_ACCOUNT_A, "cust_a", "merhaba", "mid_a1"))
    assert r.status_code == 200
    assert r.json().get("status") == "ok"

    # A'ya en az bir 'gelen' + 'giden' kaydı; B'ye HİÇBİR kayıt
    assert _conv_count(TENANT_A) >= 1
    assert _conv_count(TENANT_B) == 0

    with tenant_scope(TENANT_A):
        with get_session() as s:
            senders = {c.sender for c in s.query(Conversation).all()}
    assert "cust_a" in senders


def test_webhook_routes_to_tenant_b(client):
    client.post("/webhook", json=_ig_text_event(IG_ACCOUNT_B, "cust_b", "selam", "mid_b1"))
    assert _conv_count(TENANT_B) >= 1
    assert _conv_count(TENANT_A) == 0


def test_unknown_account_is_fail_closed(client):
    r = client.post("/webhook", json=_ig_text_event("99999999999999999", "cust_x", "merhaba", "mid_x1"))
    assert r.json() == {"status": "ignored", "reason": "unknown_account"}
    # Hiçbir tenant'a veri yazılmamalı
    assert _conv_count(TENANT_A) == 0
    assert _conv_count(TENANT_B) == 0


def test_concurrent_two_tenants_no_cross_leak(client):
    client.post("/webhook", json=_ig_text_event(IG_ACCOUNT_A, "cust_a", "merhaba", "mid_a2"))
    client.post("/webhook", json=_ig_text_event(IG_ACCOUNT_B, "cust_b", "merhaba", "mid_b2"))

    # A yalnız cust_a, B yalnız cust_b görür
    with tenant_scope(TENANT_A):
        with get_session() as s:
            a_senders = {c.sender for c in s.query(Conversation).all()}
    with tenant_scope(TENANT_B):
        with get_session() as s:
            b_senders = {c.sender for c in s.query(Conversation).all()}

    assert a_senders == {"cust_a"}
    assert b_senders == {"cust_b"}


def test_non_instagram_object_ignored(client):
    r = client.post("/webhook", json={"object": "page", "entry": []})
    assert r.json() == {"status": "ignored"}


def test_resolver_unit(env):
    # Resolver doğrudan: doğru hesap → doğru tenant; bilinmeyen → None
    assert tenant_service.resolve_tenant_by_ig_account_id(IG_ACCOUNT_A) == TENANT_A
    assert tenant_service.resolve_tenant_by_ig_account_id(IG_ACCOUNT_B) == TENANT_B
    assert tenant_service.resolve_tenant_by_ig_account_id("does-not-exist") is None
    assert tenant_service.resolve_tenant_by_ig_account_id(None) is None
