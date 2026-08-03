"""A1 — Instagram webhook X-Hub-Signature-256 imza doğrulaması.

  * Geçerli imza kabul edilir (200, işlenir).
  * Geçersiz / eksik imza reddedilir (403, işlenmez).
  * Gövde kurcalanırsa (imza artık uyuşmaz) reddedilir.

META_APP_SECRET tanımlı olduğunda imza ZORUNLUDUR (bu testler patch'ler).
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import config
import main
from Services import tenant_service
from conftest import IG_ACCOUNT_A

APP_SECRET = "test-app-secret-123"


@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.setattr(config, "META_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(main, "send_instagram_message", lambda rid, msg: None)
    monkeypatch.setattr(
        main, "general_chat",
        lambda prompt, text, sender: {"answer": "ok", "tool_call": None},
    )
    tenant_service.invalidate()
    return TestClient(main.app)


def _event(account_id=IG_ACCOUNT_A):
    return {
        "object": "instagram",
        "entry": [{
            "id": account_id, "time": 1,
            "messaging": [{
                "sender": {"id": "cust_sig"},
                "recipient": {"id": account_id},
                "timestamp": 1,
                "message": {"mid": "mid_sig_1", "text": "merhaba"},
            }],
        }],
    }


def _sign(raw):
    return "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _json_headers(extra=None):
    h = {"Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def test_valid_signature_accepted(client):
    raw = json.dumps(_event()).encode()
    r = client.post(
        "/webhook", content=raw,
        headers=_json_headers({"X-Hub-Signature-256": _sign(raw)}),
    )
    assert r.status_code == 200


def test_invalid_signature_rejected(client):
    raw = json.dumps(_event()).encode()
    r = client.post(
        "/webhook", content=raw,
        headers=_json_headers({"X-Hub-Signature-256": "sha256=deadbeef"}),
    )
    assert r.status_code == 403


def test_missing_signature_rejected(client):
    raw = json.dumps(_event()).encode()
    r = client.post("/webhook", content=raw, headers=_json_headers())
    assert r.status_code == 403


def test_tampered_body_rejected(client):
    raw = json.dumps(_event()).encode()
    sig = _sign(raw)  # orijinal gövdenin imzası
    tampered = json.dumps(_event("99999999999999999")).encode()
    r = client.post(
        "/webhook", content=tampered,
        headers=_json_headers({"X-Hub-Signature-256": sig}),
    )
    assert r.status_code == 403


def test_signature_skipped_when_secret_unset(env, monkeypatch):
    # META_APP_SECRET yoksa imza doğrulaması atlanır (mevcut davranış korunur).
    monkeypatch.setattr(config, "META_APP_SECRET", None)
    monkeypatch.setattr(main, "send_instagram_message", lambda rid, msg: None)
    monkeypatch.setattr(
        main, "general_chat",
        lambda prompt, text, sender: {"answer": "ok", "tool_call": None},
    )
    tenant_service.invalidate()
    c = TestClient(main.app)
    r = c.post("/webhook", json=_event())
    assert r.status_code == 200
