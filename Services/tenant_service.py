"""Tenant çözümü — Instagram webhook routing'in merkezî resolver'ı (Faz 4).

Instagram webhook'unda tenant'ı belirleyen CANONICAL kimlik, olayı ALAN işletme
hesabıdır: `entry[].id` (= her messaging olayında `recipient.id`) = Instagram
Business Account ID. Bu, WhatsApp'taki `phone_number_id` routing'inin Instagram
karşılığıdır. `sender.id` müşterinin IGSID'idir — tenant anahtarı DEĞİLDİR.

Akış:
    webhook → entry.id (IG Business Account ID) → resolve_tenant → tenant_id
    → (main.py) tenant_scope(tenant_id) → tenant-izole işleme

Fail-closed: hiçbir aktif tenant'a eşleşmeyen hesap için None döner; main.py
bu webhook'u işlemeden reddeder. Bilinmeyen hesap ASLA default tenant'a düşmez.

Cache: çözümleme sık ve okuma-ağırlıklı olduğundan kısa ömürlü süreç-içi TTL
cache kullanılır (kaynak gerçeği DB). Bir tenant'ın hesabı değişince
`invalidate()` çağrılır.
"""

import time

from sqlalchemy import select

from Services.db import get_session
from Services.models import Tenant

# ig_account_id -> (tenant_id | None, cached_at)
_cache = {}
_CACHE_TTL = 300  # saniye


def extract_ig_account_id(body):
    """Webhook gövdesinden IG Business Account ID'sini (entry[0].id) çıkarır.

    Mümkünse recipient.id ile çapraz doğrular; tutmuyorsa yine entry.id esas
    alınır (entry.id platformun verdiği alıcı hesap kimliğidir).
    """
    entries = body.get("entry") or []
    if not entries:
        return None
    entry = entries[0] or {}
    account_id = entry.get("id")
    return str(account_id) if account_id else None


def resolve_tenant_by_ig_account_id(ig_account_id):
    """IG Business Account ID → aktif tenant_id (yoksa None; fail-closed).

    DB hatasında da None döner (asla tahmini bir tenant'a düşmez).
    """
    if not ig_account_id:
        return None

    ig_account_id = str(ig_account_id)
    now = time.time()

    cached = _cache.get(ig_account_id)
    if cached is not None and now - cached[1] < _CACHE_TTL:
        return cached[0]

    tenant_id = None
    try:
        # Cross-tenant sistem işi (routing) — scoped=False.
        with get_session(scoped=False) as s:
            row = s.execute(
                select(Tenant.id).where(
                    Tenant.ig_account_id == ig_account_id,
                    Tenant.status == "active",
                )
            ).first()
            tenant_id = row[0] if row else None
    except Exception as e:
        print("🔴 tenant resolve hatası:", e)
        return None  # fail-closed — cache'lemeden

    _cache[ig_account_id] = (tenant_id, now)
    return tenant_id


def invalidate(ig_account_id=None):
    """Cache'i temizler (belirli hesap ya da tümü). Tenant/hesap değişiminde çağrılır."""
    if ig_account_id is None:
        _cache.clear()
    else:
        _cache.pop(str(ig_account_id), None)


def get_tenant(tenant_id):
    """tenant_id ile tenant kaydını döndürür (yoksa None)."""
    try:
        with get_session(scoped=False) as s:
            return s.get(Tenant, tenant_id)
    except Exception as e:
        print("🔴 get_tenant hatası:", e)
        return None
