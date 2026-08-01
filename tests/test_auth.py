"""Faz 2 — tenant-aware authentication kanıtları.

  * DB kullanıcısı email+parola ile doğrulanır; ctx doğru tenant_id taşır.
  * Yanlış parola reddedilir.
  * JWT round-trip: tenant_id token'a gömülür ve token'dan çözülür (request'ten değil).
  * Duplicate email reddedilir (atomik create).
  * İki tenant'ın kullanıcıları izole; her biri kendi tenant'ına çözülür.
  * Legacy .env kullanıcısı DEFAULT_TENANT (1) altında çalışmaya devam eder.
"""

import pytest

from Services import auth_service, user_service
from conftest import TENANT_A, TENANT_B, LEGACY_PASSWORD


def test_create_and_authenticate_db_user(env):
    user_service.create_user(TENANT_A, "owner@a.com", "parola12345", role="owner")

    ctx = auth_service.authenticate("owner@a.com", "parola12345")
    assert ctx is not None
    assert ctx["tenant_id"] == TENANT_A
    assert ctx["email"] == "owner@a.com"
    assert ctx["role"] == "owner"


def test_authenticate_wrong_password(env):
    user_service.create_user(TENANT_A, "owner@a.com", "parola12345")
    assert auth_service.authenticate("owner@a.com", "yanlis-parola") is None


def test_duplicate_email_rejected(env):
    user_service.create_user(TENANT_A, "dup@x.com", "parola12345")
    with pytest.raises(ValueError):
        user_service.create_user(TENANT_B, "dup@x.com", "parola12345")


def test_token_roundtrip_carries_tenant(env):
    user_service.create_user(TENANT_B, "owner@b.com", "parola12345")
    ctx = auth_service.authenticate("owner@b.com", "parola12345")

    token = auth_service.create_token(ctx)
    decoded = auth_service.verify_token(token)

    assert decoded["tenant_id"] == TENANT_B
    assert decoded["email"] == "owner@b.com"


def test_tenant_comes_from_token_signature_not_forgeable(env):
    # Farklı bir secret ile imzalanmış token reddedilir (tenant zorlanamaz).
    import jwt as _jwt

    forged = _jwt.encode(
        {"sub": "attacker@x.com", "tid": TENANT_A, "role": "owner",
         "iat": 0, "exp": 9999999999},
        "WRONG-SECRET", algorithm="HS256",
    )
    assert auth_service.verify_token(forged) is None


def test_two_tenants_users_isolated(env):
    user_service.create_user(TENANT_A, "a@a.com", "parola12345")
    user_service.create_user(TENANT_B, "b@b.com", "parola12345")

    ctx_a = auth_service.authenticate("a@a.com", "parola12345")
    ctx_b = auth_service.authenticate("b@b.com", "parola12345")

    assert ctx_a["tenant_id"] == TENANT_A
    assert ctx_b["tenant_id"] == TENANT_B
    # A'nın parolası B'nin hesabını açamaz
    assert auth_service.authenticate("b@b.com", "wrong") is None


def test_legacy_env_login_maps_to_default_tenant(env):
    ctx = auth_service.authenticate("admin", LEGACY_PASSWORD)
    assert ctx is not None
    assert ctx["tenant_id"] == 1
    assert ctx["role"] == "owner"
