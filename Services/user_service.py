"""Panel kullanıcıları — tenant-aware kullanıcı yönetimi (Faz 2).

Kullanıcılar kök `User` modelinde tutulur; her kullanıcı bir tenant'a bağlıdır.
Email platform genelinde tekildir. Login sırasında email ile arama tenant
bağlamı OLMADAN yapılır (scoped=False) — tenant kimliği kullanıcının kaydından
(tenant_id) türetilir, request'ten gelen değere GÜVENİLMEZ.

Kullanıcı oluşturma atomiktir ve duplicate email'i reddeder (orphan bırakmaz).
"""

from datetime import datetime

from sqlalchemy import select

from Services.db import get_session
from Services.models import User
from Services.auth_service import hash_password, verify_password


def _norm_email(email):
    return (email or "").strip().lower()


def get_user_by_email(email):
    """Email ile kullanıcı kaydını döndürür (yoksa None). Cross-tenant arama."""
    email = _norm_email(email)
    if not email:
        return None
    with get_session(scoped=False) as s:
        return s.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()


def email_exists(email):
    return get_user_by_email(email) is not None


def create_user(tenant_id, email, password, role="owner"):
    """Yeni panel kullanıcısı oluşturur (atomik). Duplicate email → ValueError.

    Döner: {id, tenant_id, email, role}.
    """
    email = _norm_email(email)
    if not email or "@" not in email:
        raise ValueError("Geçerli bir email gerekli.")
    if not password or len(password) < 8:
        raise ValueError("Parola en az 8 karakter olmalı.")

    with get_session(scoped=False) as s:
        existing = s.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("Bu email zaten kayıtlı.")

        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password(password),
            role=role,
            created_at=datetime.now(),
        )
        s.add(user)
        s.flush()
        return {
            "id": user.id,
            "tenant_id": user.tenant_id,
            "email": user.email,
            "role": user.role,
        }


def authenticate_db_user(email, password):
    """DB kullanıcısını doğrular. Başarılıysa auth context dict, değilse None.

    Kullanıcı bulunamasa bile bcrypt karşılaştırması yapılır (timing sızıntısı yok).
    """
    email = _norm_email(email)
    user = get_user_by_email(email)

    if user is None:
        # Sabit süreli sahte doğrulama — "kullanıcı var mı" zamanlamadan sızmasın.
        verify_password(password or "", None)
        return None

    if not verify_password(password or "", user.password_hash):
        return None

    return {
        "user_id": user.id,
        "tenant_id": user.tenant_id,
        "email": user.email,
        "role": user.role,
    }
