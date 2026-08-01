"""Onboarding — yeni tenant'ın ATOMİK oluşturulması (Faz 8).

Akış: tenant → owner user → tenant settings (opsiyonel) tek transaction'da
oluşturulur. Bir adım başarısızsa hiçbiri yazılmaz (orphan tenant bırakmaz).
Duplicate email ve duplicate Instagram hesabı reddedilir.

Bu aşamada public self-signup yoktur; tenant oluşturma platform operatörü
(super-admin) üzerinden yapılır. Instagram bağlantısı ve tam ayarlar, mevcut
Kurulum (setup) sihirbazı ile tenant içinde tamamlanır.
"""

from datetime import datetime

from sqlalchemy import select

from Services.db import get_session
from Services.models import Tenant, User, Setting
from Services.auth_service import hash_password
from Services.settings_service import is_secret_key
from Services import crypto_service
from Services import tenant_service


def _norm_email(email):
    return (email or "").strip().lower()


def create_tenant(name, owner_email, owner_password,
                  ig_account_id=None, initial_settings=None, role="owner"):
    """Yeni tenant + owner user (+ opsiyonel ayarlar) ATOMİK oluşturur.

    Döner: {tenant_id, user_id, email}. Hata: ValueError (doğrulama/çakışma).
    """
    name = (name or "").strip()
    email = _norm_email(owner_email)
    ig = str(ig_account_id).strip() if ig_account_id else None

    if not name:
        raise ValueError("Tenant adı zorunlu.")
    if not email or "@" not in email:
        raise ValueError("Geçerli bir owner email'i gerekli.")
    if not owner_password or len(owner_password) < 8:
        raise ValueError("Parola en az 8 karakter olmalı.")

    now = datetime.now()

    # Tek transaction (scoped=False — cross-tenant sistem işi). Herhangi bir
    # adım hata verirse get_session rollback yapar → atomiklik.
    with get_session(scoped=False) as s:
        if s.execute(select(User).where(User.email == email)).scalar_one_or_none():
            raise ValueError("Bu email zaten kayıtlı.")

        if ig and s.execute(
            select(Tenant).where(Tenant.ig_account_id == ig)
        ).scalar_one_or_none():
            raise ValueError("Bu Instagram hesabı zaten başka bir tenant'a bağlı.")

        tenant = Tenant(name=name, ig_account_id=ig, status="active", created_at=now)
        s.add(tenant)
        s.flush()  # tenant.id

        user = User(
            tenant_id=tenant.id, email=email,
            password_hash=hash_password(owner_password), role=role, created_at=now,
        )
        s.add(user)
        s.flush()

        if initial_settings:
            for skey, svalue in initial_settings.items():
                store_value = svalue
                if is_secret_key(skey) and svalue not in (None, ""):
                    store_value = crypto_service.encrypt(svalue)
                # scoped=False → tenant_id'yi AÇIKÇA veriyoruz (otomatik damga yok).
                s.add(Setting(
                    tenant_id=tenant.id, skey=skey,
                    svalue=store_value, updated_at=now,
                ))

        result = {"tenant_id": tenant.id, "user_id": user.id, "email": email}

    # Yeni hesap eşleşmesi resolver cache'inde stale kalmasın.
    if ig:
        tenant_service.invalidate(ig)

    return result


def create_superadmin(email, password, tenant_name="Platform"):
    """Platform operatörü (super-admin) + platform tenant'ı oluşturur (bootstrap).

    Zaten bir super-admin varsa hata verir (idempotent bootstrap değil, tekil).
    """
    with get_session(scoped=False) as s:
        exists = s.execute(
            select(User).where(User.role == "superadmin")
        ).first()
        if exists:
            raise ValueError("Zaten bir super-admin mevcut.")

    return create_tenant(tenant_name, email, password, role="superadmin")
