"""Landing page + lead capture (talep formu) duman testleri."""

from fastapi.testclient import TestClient

import main
from Services.db import get_session
from Services.models import SignupRequest


def test_landing_served(env):
    c = TestClient(main.app)
    r = c.get("/")
    assert r.status_code == 200
    assert "InstaAgent" in r.text
    assert c.get("/instagent").status_code == 200


def test_healthz(env):
    c = TestClient(main.app)
    assert c.get("/healthz").json() == {"status": "ok"}


def test_signup_capture(env):
    c = TestClient(main.app)
    r = c.post("/kayit", json={
        "store_name": "Nilnur Moda", "contact_name": "Ayşe Demir",
        "email": "ayse@nilnur.com", "instagram": "@nilnurmoda",
        "message": "Denemek istiyorum",
    })
    assert r.json() == {"ok": True}
    with get_session(scoped=False) as s:
        rows = s.query(SignupRequest).all()
    assert len(rows) == 1
    assert rows[0].store_name == "Nilnur Moda"
    assert rows[0].status == "new"


def test_signup_validation(env):
    c = TestClient(main.app)
    r = c.post("/kayit", json={"store_name": "", "contact_name": "", "email": "bad"})
    assert r.status_code == 400
    assert r.json()["ok"] is False
