"""Faz B3 — kredensiyel/mağaza değişiminde tenant-scoped cache invalidation.

Kurulum sihirbazından (save_section) yeni anahtar/mağaza yazıldığında eski
OpenAI client'ı, İKAS token/ürün önbelleği ve hesap→tenant resolver cache'i
kullanılmaya devam etmemeli. İzolasyon: bir tenant'ın invalidation'ı diğerinin
cache'ini bozmaz.
"""

import time

from Services.db import tenant_scope, get_session
from Services.models import Tenant
from Services import setup_service, ikas_service, tenant_service
from conftest import TENANT_A, TENANT_B, IG_ACCOUNT_A


# NOT: OpenAI artık SİSTEM anahtarıdır (.env); kurulumdan yazılmadığı için
# setup-driven client invalidation testi kaldırıldı. İKAS ve resolver hâlâ
# müşteri-ayarı olduğundan onların invalidation'ı test edilir.


def test_ikas_cache_invalidated_on_creds_change(env):
    now = time.time()
    ikas_service._token_cache[TENANT_A] = {"access_token": "x", "expires_at": now + 9999}
    ikas_service._token_cache[TENANT_B] = {"access_token": "y", "expires_at": now + 9999}
    ikas_service.ikas_search_cache[(TENANT_A, "etek")] = {"context": "c", "product_id": "p", "created_at": now}
    ikas_service.ikas_search_cache[(TENANT_B, "etek")] = {"context": "c", "product_id": "p", "created_at": now}

    with tenant_scope(TENANT_A):
        res = setup_service.save_section("ikas", {
            "IKAS_STORE_NAME": "store", "IKAS_CLIENT_ID": "cid", "IKAS_CLIENT_SECRET": "sec",
        })

    assert res["ok"] is True
    assert TENANT_A not in ikas_service._token_cache
    assert (TENANT_A, "etek") not in ikas_service.ikas_search_cache
    # Başka tenant'ın token/ürün önbelleği korunur
    assert TENANT_B in ikas_service._token_cache
    assert (TENANT_B, "etek") in ikas_service.ikas_search_cache


def test_resolver_cache_invalidated_on_ig_account_change(env):
    # Resolver cache'ini doldur
    assert tenant_service.resolve_tenant_by_ig_account_id(IG_ACCOUNT_A) == TENANT_A
    assert IG_ACCOUNT_A in tenant_service._cache

    with tenant_scope(TENANT_A):
        res = setup_service.save_section("instagram", {"IG_ACCOUNT_ID": "17800000000000777"})

    assert res["ok"] is True
    # Resolver cache temizlendi (eski hesap eşlemesi eskimesin)
    assert tenant_service._cache == {}
    # Yeni hesap kimliği routing sütununa yansıdı → resolver yeni ID'yi bulur
    assert tenant_service.resolve_tenant_by_ig_account_id("17800000000000777") == TENANT_A
    with get_session(scoped=False) as s:
        assert s.get(Tenant, TENANT_A).ig_account_id == "17800000000000777"
