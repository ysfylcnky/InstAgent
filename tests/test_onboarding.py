"""Faz 8 — onboarding (atomik tenant oluşturma) kanıtları."""

import pytest
from sqlalchemy import select

from Services.db import get_session, tenant_scope
from Services.models import Tenant, User, Setting
from Services import onboarding_service, auth_service, tenant_service, settings_service
from conftest import _bcrypt  # noqa (env hazır)


def _tenant_count():
    with get_session(scoped=False) as s:
        return len(s.execute(select(Tenant)).all())


def test_create_tenant_atomic_with_owner_and_settings(env):
    before = _tenant_count()

    res = onboarding_service.create_tenant(
        name="Yeni Butik",
        owner_email="Owner@Yeni.com",
        owner_password="parola12345",
        ig_account_id="17899999999999999",
        initial_settings={"IG_ACCESS_TOKEN": "yeni_secret_token", "STORE_IBAN": "TR..."},
    )

    assert _tenant_count() == before + 1
    tid = res["tenant_id"]

    # Owner login olabilir ve doğru tenant'a çözülür
    ctx = auth_service.authenticate("owner@yeni.com", "parola12345")
    assert ctx is not None and ctx["tenant_id"] == tid

    # Secret şifreli saklandı
    with get_session(scoped=False) as s:
        row = s.execute(
            select(Setting.svalue).where(
                Setting.tenant_id == tid, Setting.skey == "IG_ACCESS_TOKEN"
            )
        ).first()
    assert row[0].startswith("enc:v1:")
    # Doğru çözülüyor
    with tenant_scope(tid):
        assert settings_service.get_stored_setting("IG_ACCESS_TOKEN") == "yeni_secret_token"

    # ig_account_id ile resolver yeni tenant'ı bulur
    assert tenant_service.resolve_tenant_by_ig_account_id("17899999999999999") == tid


def test_duplicate_email_is_atomic_no_orphan(env):
    onboarding_service.create_tenant("T1", "dup@x.com", "parola12345")
    before = _tenant_count()

    with pytest.raises(ValueError):
        onboarding_service.create_tenant("T2", "dup@x.com", "parola12345")

    # İkinci tenant OLUŞMAMALI (atomik) — orphan yok
    assert _tenant_count() == before


def test_duplicate_ig_account_rejected(env):
    onboarding_service.create_tenant("T1", "a@x.com", "parola12345",
                                     ig_account_id="17800000000000009")
    with pytest.raises(ValueError):
        onboarding_service.create_tenant("T2", "b@x.com", "parola12345",
                                         ig_account_id="17800000000000009")


def test_created_tenants_are_isolated(env):
    r1 = onboarding_service.create_tenant("Store1", "s1@x.com", "parola12345")
    r2 = onboarding_service.create_tenant("Store2", "s2@x.com", "parola12345")

    with tenant_scope(r1["tenant_id"]):
        settings_service.save_stored_settings({"STORE_IBAN": "TR-STORE1"})
    with tenant_scope(r2["tenant_id"]):
        settings_service.save_stored_settings({"STORE_IBAN": "TR-STORE2"})

    with tenant_scope(r1["tenant_id"]):
        assert settings_service.get_stored_setting("STORE_IBAN") == "TR-STORE1"
    with tenant_scope(r2["tenant_id"]):
        assert settings_service.get_stored_setting("STORE_IBAN") == "TR-STORE2"


def test_validation_errors(env):
    with pytest.raises(ValueError):
        onboarding_service.create_tenant("", "a@x.com", "parola12345")
    with pytest.raises(ValueError):
        onboarding_service.create_tenant("T", "not-an-email", "parola12345")
    with pytest.raises(ValueError):
        onboarding_service.create_tenant("T", "a@x.com", "short")
