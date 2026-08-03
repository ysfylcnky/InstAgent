"""Faz 3 — tenant settings + secret yönetimi izolasyon kanıtları.

  * Secret ayar DB'de ŞİFRELİ saklanır (düz metin değil).
  * A'nın sırrı B tarafından okunamaz.
  * Tekil okuma secret'ı çözer; toplu okuma ham (şifreli) döndürür.
  * Non-secret ayar düz metin saklanır.
  * UPSERT: aynı anahtar güncellenir, çoğaltılmaz.
"""

from sqlalchemy import select

from Services.db import get_session, tenant_scope
from Services.models import Setting, Tenant
from Services import settings_service as ss
from Services import setup_service
from conftest import TENANT_A, TENANT_B


def _raw_svalue(tenant_id, skey):
    """DB'de gerçekte ne yazılı — scope=False ile ham okuma."""
    with get_session(scoped=False) as s:
        row = s.execute(
            select(Setting.svalue).where(
                Setting.tenant_id == tenant_id, Setting.skey == skey
            )
        ).first()
        return row[0] if row else None


def test_secret_stored_encrypted_and_decrypted_on_read(env):
    with tenant_scope(TENANT_A):
        ss.save_stored_settings({"IG_ACCESS_TOKEN": "A_TOKEN_PLAIN"})

    # DB'de ham değer ŞİFRELİ olmalı (düz metin sızmamalı)
    raw = _raw_svalue(TENANT_A, "IG_ACCESS_TOKEN")
    assert raw is not None
    assert raw != "A_TOKEN_PLAIN"
    assert raw.startswith("enc:v1:")

    # Tekil okuma çözer
    with tenant_scope(TENANT_A):
        assert ss.get_stored_setting("IG_ACCESS_TOKEN") == "A_TOKEN_PLAIN"


def test_secret_cross_tenant_isolation(env):
    with tenant_scope(TENANT_A):
        ss.save_stored_settings({"IG_ACCESS_TOKEN": "A_TOKEN"})
    with tenant_scope(TENANT_B):
        ss.save_stored_settings({"IG_ACCESS_TOKEN": "B_TOKEN"})

    with tenant_scope(TENANT_A):
        assert ss.get_stored_setting("IG_ACCESS_TOKEN") == "A_TOKEN"
    with tenant_scope(TENANT_B):
        assert ss.get_stored_setting("IG_ACCESS_TOKEN") == "B_TOKEN"

    # A scope'unda toplu okuma B'nin anahtarını içermez ve secret ham (şifreli)
    with tenant_scope(TENANT_A):
        allset = ss.get_all_stored_settings()
    assert set(allset.keys()) == {"IG_ACCESS_TOKEN"}
    assert allset["IG_ACCESS_TOKEN"].startswith("enc:v1:")  # toplu okuma çözmez


def test_non_secret_stored_plaintext(env):
    with tenant_scope(TENANT_A):
        ss.save_stored_settings({"STORE_IBAN": "TR000000000000000000000000"})
    raw = _raw_svalue(TENANT_A, "STORE_IBAN")
    assert raw == "TR000000000000000000000000"  # düz metin


def test_upsert_updates_not_duplicates(env):
    with tenant_scope(TENANT_A):
        ss.save_stored_settings({"MODEL_NAME": "gpt-4.1-mini"})
        ss.save_stored_settings({"MODEL_NAME": "gpt-4o"})
        assert ss.get_stored_setting("MODEL_NAME") == "gpt-4o"

    with get_session(scoped=False) as s:
        cnt = s.execute(
            select(Setting).where(
                Setting.tenant_id == TENANT_A, Setting.skey == "MODEL_NAME"
            )
        ).all()
    assert len(cnt) == 1


# ----------------------------------------------------------------------
# Faz B1 — Kurulum sihirbazı tenant credential'larını .env yerine DB'ye yazar.
# ----------------------------------------------------------------------

def test_setup_writes_creds_to_tenant_settings(env):
    with tenant_scope(TENANT_A):
        res = setup_service.save_section("instagram", {
            "IG_ACCOUNT_ID": "17841400000000000",
            "IG_ACCESS_TOKEN": "IG_SECRET_TOKEN",
            "IG_API_BASE": "graph.instagram.com",
        })

    assert res["ok"] is True
    # Ayar (settings) hedefli → anında geçerli, restart gerekmez
    assert res.get("restart_required") is False

    # Düz metin ayarlar doğru tenant'ta
    assert _raw_svalue(TENANT_A, "IG_ACCOUNT_ID") == "17841400000000000"
    assert _raw_svalue(TENANT_A, "IG_API_BASE") == "graph.instagram.com"

    # Access token DB'de ŞİFRELİ (düz metin sızmaz)
    raw_tok = _raw_svalue(TENANT_A, "IG_ACCESS_TOKEN")
    assert raw_tok is not None and raw_tok.startswith("enc:v1:")
    with tenant_scope(TENANT_A):
        assert ss.get_stored_setting("IG_ACCESS_TOKEN") == "IG_SECRET_TOKEN"

    # Başka tenant hiçbir şey almadı
    assert _raw_svalue(TENANT_B, "IG_ACCOUNT_ID") is None

    # Routing anahtarı (Tenant.ig_account_id sütunu) da senkronlandı
    with get_session(scoped=False) as s:
        assert s.get(Tenant, TENANT_A).ig_account_id == "17841400000000000"


def test_setup_rejects_ig_account_bound_to_other_tenant(env):
    # B'nin IG hesabını A'ya bağlamaya çalışmak reddedilmeli (çapraz ele geçirme yok).
    from conftest import IG_ACCOUNT_B
    with tenant_scope(TENANT_A):
        res = setup_service.save_section("instagram", {"IG_ACCOUNT_ID": IG_ACCOUNT_B})
    assert res["ok"] is False
