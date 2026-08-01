"""Migration runner — temiz kurulum + idempotency + backfill (SQLite yolu).

MySQL'e özgü ALTER adımları burada test edilmez (dialect guard'lı); ancak
create_all + default tenant + backfill mantığı doğrulanır.
"""

from datetime import datetime

from Services import db as db_module
from Services.db import get_session, tenant_scope
from Services.models import Base, Tenant, Conversation
from migrations import run as migration


def _fresh_db():
    db_module.reset_engine()
    db_module.set_default_tenant_fallback(True)
    engine = db_module.get_engine()
    Base.metadata.drop_all(engine)
    # create_all YAPMA — migration'ın kendisi kursun.
    return engine


def test_apply_creates_default_tenant_and_is_idempotent():
    _fresh_db()

    migration.apply(tenant_name="Mumi", ig_account_id="17812345678901234")

    with get_session(scoped=False) as s:
        tenants = s.query(Tenant).all()
        assert len(tenants) == 1
        assert tenants[0].id == 1
        assert tenants[0].name == "Mumi"
        assert tenants[0].ig_account_id == "17812345678901234"

    # İkinci kez uygulanınca hata vermez, tenant çoğalmaz (idempotent)
    migration.apply(tenant_name="Mumi", ig_account_id="17812345678901234")
    with get_session(scoped=False) as s:
        assert s.query(Tenant).count() == 1


def test_default_tenant_bridge_reads_existing_data():
    # Migration sonrası, scope belirtilmeden (tek-tenant köprüsü) tenant 1 verisi okunur
    _fresh_db()
    migration.apply(tenant_name="Mumi")

    with tenant_scope(1):
        with get_session() as s:
            s.add(Conversation(timestamp=datetime.now(), sender="igsid",
                               direction="gelen", content="mevcut mumi mesajı"))

    # scope YOK → fallback tenant 1 → mevcut veri görünür (geriye dönük uyum)
    with get_session() as s:
        rows = s.query(Conversation).all()
        assert len(rows) == 1
        assert rows[0].content == "mevcut mumi mesajı"
        assert rows[0].tenant_id == 1
