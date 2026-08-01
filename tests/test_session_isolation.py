"""Faz 5 — session / cache / state izolasyon kanıtları.

  * Aynı IGSID iki tenant'ta ayrı oturuma sahiptir (namespace: {tenant}:{igsid}).
  * Dedup (message_id) tenant'a göre namespace'lidir — aynı mid iki tenant'ta
    ayrı sayılır.
  * İKAS ürün cache'i tenant'a göre namespace'lidir; bir tenant'ın ürünü
    diğerine sızmaz.
"""

from Services.db import tenant_scope
from Services.session_store import SessionRegistry, InMemorySessionStore, new_session
from Services import message_service, ikas_service
from conftest import TENANT_A, TENANT_B


def test_session_namespaced_by_tenant(env):
    reg = SessionRegistry(InMemorySessionStore())

    with tenant_scope(TENANT_A):
        reg.begin_request()
        reg["shared_igsid"] = new_session()
        reg["shared_igsid"]["active_url"] = "ikas:A_PRODUCT"
        reg.flush()

    with tenant_scope(TENANT_B):
        reg.begin_request()
        # B, A'nın oturumunu GÖRMEZ
        assert "shared_igsid" not in reg
        reg["shared_igsid"] = new_session()
        reg["shared_igsid"]["active_url"] = "ikas:B_PRODUCT"
        reg.flush()

    with tenant_scope(TENANT_A):
        reg.begin_request()
        assert reg["shared_igsid"]["active_url"] == "ikas:A_PRODUCT"
        reg.flush()

    with tenant_scope(TENANT_B):
        reg.begin_request()
        assert reg["shared_igsid"]["active_url"] == "ikas:B_PRODUCT"
        reg.flush()


def test_dedup_namespaced_by_tenant(env):
    message_service.processed_messages.clear()

    with tenant_scope(TENANT_A):
        assert message_service.is_duplicate("mid_same") is False  # A'da ilk kez
        assert message_service.is_duplicate("mid_same") is True   # A'da tekrar

    with tenant_scope(TENANT_B):
        # Aynı mid B'de İLK kez — A'nın kaydı B'yi etkilemez
        assert message_service.is_duplicate("mid_same") is False
        assert message_service.is_duplicate("mid_same") is True


def test_ikas_cache_namespaced_by_tenant(env, monkeypatch):
    calls = {"count": 0}

    def fake_search(name):
        calls["count"] += 1
        from Services.db import get_current_tenant
        return {"id": f"{get_current_tenant()}-{name}", "name": name, "variants": [{}]}

    monkeypatch.setattr(ikas_service, "search_product_by_name", fake_search)
    monkeypatch.setattr(ikas_service, "build_ikas_ai_context",
                        lambda p: {"name": p["id"]})

    with tenant_scope(TENANT_A):
        ctx_a, pid_a = ikas_service.get_cached_ikas_context("etek")
    with tenant_scope(TENANT_B):
        ctx_b, pid_b = ikas_service.get_cached_ikas_context("etek")

    # Her tenant kendi ürününü alır (çapraz sızma yok)
    assert pid_a == "1-etek"
    assert pid_b == "2-etek"
    assert ctx_a["name"] == "1-etek"
    assert ctx_b["name"] == "2-etek"
    assert calls["count"] == 2  # her tenant için ayrı arama

    # A tekrar sorunca CACHE HIT (arama sayısı artmaz) ve hâlâ A'nın ürünü
    with tenant_scope(TENANT_A):
        ctx_a2, pid_a2 = ikas_service.get_cached_ikas_context("etek")
    assert pid_a2 == "1-etek"
    assert calls["count"] == 2
