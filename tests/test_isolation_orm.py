"""Faz 1 — ORM seviyesinde tenant izolasyonu KANITLARI.

İki bağımsız tenant (A=1, B=2) ile:
  * A, B'nin verisini okuyamaz (her tablo).
  * INSERT'te tenant_id otomatik damgalanır.
  * Çapraz-tenant insert reddedilir.
  * Unscoped session (scoped=False) bilinçli olarak tüm tenant'ları görür.
  * Scoped ama tenant yok → fail-closed (hiçbir satır dönmez).
  * Secret'lar Fernet ile şifrelenir; DB'de düz metin değildir.
"""

import pytest

from Services.db import get_session, tenant_scope, TenantScopeError
from Services.models import Conversation, Customer, Order, UsageLog, Setting
from Services import crypto_service
from conftest import (
    TENANT_A, TENANT_B,
    seed_conversation, seed_customer, seed_order, seed_usage, seed_setting,
)


def _count(model, tenant_id):
    with tenant_scope(tenant_id):
        with get_session() as s:
            return s.query(model).count()


def test_conversations_cross_tenant_read_blocked(env):
    seed_conversation(TENANT_A, "igsid_a", "A mesajı")
    seed_conversation(TENANT_A, "igsid_a", "A mesajı 2")
    seed_conversation(TENANT_B, "igsid_b", "B mesajı")

    assert _count(Conversation, TENANT_A) == 2
    assert _count(Conversation, TENANT_B) == 1

    # A scope'unda B'nin içeriği ASLA görünmez
    with tenant_scope(TENANT_A):
        with get_session() as s:
            contents = [c.content for c in s.query(Conversation).all()]
    assert "B mesajı" not in contents
    assert set(contents) == {"A mesajı", "A mesajı 2"}


def test_customers_same_igsid_isolated(env):
    # Aynı IGSID iki tenant'ta — çakışmamalı, çapraz görünmemeli
    seed_customer(TENANT_A, "shared_igsid", "Ali (A)")
    seed_customer(TENANT_B, "shared_igsid", "Veli (B)")

    with tenant_scope(TENANT_A):
        with get_session() as s:
            rows = s.query(Customer).all()
    assert len(rows) == 1
    assert rows[0].ad_soyad == "Ali (A)"

    with tenant_scope(TENANT_B):
        with get_session() as s:
            rows = s.query(Customer).all()
    assert len(rows) == 1
    assert rows[0].ad_soyad == "Veli (B)"


def test_orders_and_usage_isolated(env):
    seed_order(TENANT_A, "igsid_a", "Abaya")
    seed_order(TENANT_B, "igsid_b", "Trençkot")
    seed_usage(TENANT_A, "igsid_a", cost=0.05)
    seed_usage(TENANT_B, "igsid_b", cost=0.09)

    assert _count(Order, TENANT_A) == 1
    assert _count(Order, TENANT_B) == 1

    with tenant_scope(TENANT_A):
        with get_session() as s:
            assert [o.urun for o in s.query(Order).all()] == ["Abaya"]
            total = sum(u.cost for u in s.query(UsageLog).all())
    assert round(total, 2) == 0.05


def test_auto_stamp_on_insert(env):
    # tenant_id VERİLMEDEN eklenen kayıt aktif tenant'la damgalanır
    with tenant_scope(TENANT_B):
        with get_session() as s:
            c = Conversation(
                timestamp=__import__("datetime").datetime.now(),
                sender="x", direction="gelen", content="damga testi",
            )
            s.add(c)
        # commit sonrası
        with get_session() as s:
            row = s.query(Conversation).filter_by(content="damga testi").one()
            assert row.tenant_id == TENANT_B


def test_cross_tenant_insert_rejected(env):
    # A scope'undayken B'ye ait kayıt eklemeye çalışmak reddedilir
    with tenant_scope(TENANT_A):
        with pytest.raises(TenantScopeError):
            with get_session() as s:
                s.add(Order(
                    tenant_id=TENANT_B,
                    timestamp=__import__("datetime").datetime.now(),
                    customer_phone="igsid_b", urun="hack", adet=1, is_update=0,
                ))


def test_unscoped_bypass_sees_all(env):
    seed_conversation(TENANT_A, "igsid_a", "A")
    seed_conversation(TENANT_B, "igsid_b", "B")

    with get_session(scoped=False) as s:
        assert s.query(Conversation).count() == 2


def test_scoped_without_tenant_is_fail_closed(env):
    seed_conversation(TENANT_A, "igsid_a", "A")
    seed_conversation(TENANT_B, "igsid_b", "B")

    # Fallback KAPALI + scope YOK → scoped sorgu hiçbir tenant-owned satır döndürmez
    env.set_default_tenant_fallback(False)
    try:
        with get_session() as s:  # scoped=True ama current_tenant yok
            assert s.query(Conversation).count() == 0
            assert s.query(Order).count() == 0
    finally:
        env.set_default_tenant_fallback(True)


def test_settings_secret_isolation_and_encryption(env):
    # Secret değer ŞİFRELİ saklanır ve tenant'lar arası okunamaz
    token_a = crypto_service.encrypt("A_GIZLI_TOKEN")
    token_b = crypto_service.encrypt("B_GIZLI_TOKEN")

    assert crypto_service.is_encrypted(token_a)
    assert "A_GIZLI_TOKEN" not in token_a  # düz metin sızmıyor

    seed_setting(TENANT_A, "IG_ACCESS_TOKEN", token_a)
    seed_setting(TENANT_B, "IG_ACCESS_TOKEN", token_b)

    # A yalnız kendi (şifreli) değerini görür; çözünce kendi sırrını alır
    with tenant_scope(TENANT_A):
        with get_session() as s:
            row = s.query(Setting).filter_by(skey="IG_ACCESS_TOKEN").one()
    assert crypto_service.decrypt(row.svalue) == "A_GIZLI_TOKEN"

    # B'nin scope'unda A'nın satırı hiç görünmez
    with tenant_scope(TENANT_B):
        with get_session() as s:
            rows = s.query(Setting).filter_by(skey="IG_ACCESS_TOKEN").all()
    assert len(rows) == 1
    assert crypto_service.decrypt(rows[0].svalue) == "B_GIZLI_TOKEN"


def test_decrypt_tampered_fails_closed(env):
    token = crypto_service.encrypt("hassas")
    tampered = token[:-4] + "AAAA"
    with pytest.raises(crypto_service.CryptoError):
        crypto_service.decrypt(tampered)
