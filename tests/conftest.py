"""Test ortamı — in-memory SQLite + iki bağımsız tenant (A, B).

İzolasyon testleri gerçek MySQL/Redis olmadan çalışır: modeller dialect-bağımsız
olduğundan SQLite üzerinde birebir şema kurulur. Env değişkenleri, herhangi bir
Services.* modülü import edilmeden ÖNCE (bu dosya import anında) ayarlanır.
"""

import os

# --- Testler için ortam: gerçek .env/MySQL yerine SQLite + sabit kripto anahtarı ---
os.environ.setdefault("DATABASE_URL", "sqlite://")  # in-memory (StaticPool)
# Sabit, testlere özel Fernet anahtarı (gerçek anahtar DEĞİL).
os.environ.setdefault("ENCRYPTION_KEY", "nnFH1p8y0lU4kM8kO3s2s3fJ9v1c9m5s3o6p7q8r0s4=")
# Auth (JWT + legacy fallback) için deterministik test ortamı.
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
os.environ.setdefault("DASHBOARD_USER", "admin")
# main.py'yi (FastAPI app) import edebilmek için: OpenAI client'ı boş anahtarla
# kurulmasın; Redis'e (olmayan) bağlanmaya çalışıp gecikmesin (InMemory'e düşsün).
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-not-used")
os.environ.setdefault("REDIS_URL", "")

import bcrypt as _bcrypt  # noqa: E402

LEGACY_PASSWORD = "legacy-pass-123"
os.environ.setdefault(
    "DASHBOARD_PASSWORD_HASH",
    _bcrypt.hashpw(LEGACY_PASSWORD.encode(), _bcrypt.gensalt()).decode(),
)

from datetime import datetime  # noqa: E402

import pytest  # noqa: E402

from Services import db as db_module  # noqa: E402
from Services.db import get_session, tenant_scope  # noqa: E402
from Services.models import (  # noqa: E402
    Base,
    Tenant,
    Conversation,
    Customer,
    Order,
    UsageLog,
    Setting,
)

TENANT_A = 1
TENANT_B = 2


# İki tenant'ın Instagram Business Account ID'leri (webhook routing anahtarı).
IG_ACCOUNT_A = "17800000000000001"
IG_ACCOUNT_B = "17800000000000002"


@pytest.fixture()
def env():
    """Temiz şema + iki tenant. Her test kendi in-memory DB'siyle başlar."""
    db_module.reset_engine()
    db_module.set_default_tenant_fallback(True)
    engine = db_module.get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    # Kök tenant'lar — scoped=False (cross-tenant sistem işi).
    with get_session(scoped=False) as s:
        s.add(Tenant(id=TENANT_A, name="Mumi", ig_account_id=IG_ACCOUNT_A))
        s.add(Tenant(id=TENANT_B, name="Butik B", ig_account_id=IG_ACCOUNT_B))

    # Süreç-global cache/state'leri her test için sıfırla (test hijyeni).
    try:
        from Services import tenant_service
        tenant_service.invalidate()
    except Exception:
        pass
    try:
        from Services import message_service
        message_service.processed_messages.clear()
    except Exception:
        pass
    try:
        from Services import setup_service
        setup_service.reset_setup_cache()
    except Exception:
        pass
    try:
        from Services import ikas_service
        ikas_service.invalidate()
    except Exception:
        pass
    try:
        from Services import openai_service
        openai_service.invalidate_client()
    except Exception:
        pass

    yield db_module


def seed_conversation(tenant_id, sender, content, direction="gelen"):
    with tenant_scope(tenant_id):
        with get_session() as s:
            s.add(Conversation(
                timestamp=datetime.now(), sender=sender,
                direction=direction, content=content,
            ))


def seed_customer(tenant_id, phone, ad_soyad):
    with tenant_scope(tenant_id):
        with get_session() as s:
            now = datetime.now()
            s.add(Customer(phone=phone, ad_soyad=ad_soyad,
                           first_seen=now, last_seen=now))


def seed_order(tenant_id, phone, urun):
    with tenant_scope(tenant_id):
        with get_session() as s:
            s.add(Order(timestamp=datetime.now(), customer_phone=phone,
                        urun=urun, adet=1, is_update=0))


def seed_usage(tenant_id, sender, cost=0.01):
    with tenant_scope(tenant_id):
        with get_session() as s:
            s.add(UsageLog(
                timestamp=datetime.now(), sender=sender, model="gpt-4.1-mini",
                prompt_tokens=10, completion_tokens=5, total_tokens=15,
                cost=cost, response_time=0.5,
            ))


def seed_setting(tenant_id, skey, svalue):
    with tenant_scope(tenant_id):
        with get_session() as s:
            s.add(Setting(skey=skey, svalue=svalue, updated_at=datetime.now()))
