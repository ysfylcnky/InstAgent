"""Mesaj tekilleştirme (deduplication) — tenant-namespaced, dağıtık-güvenli.

Instagram bir olayı ağ koşullarına göre birden çok kez teslim edebilir; aynı
mesaj iki kez işlenmesin diye message_id (mid) izlenir.

Multi-tenant / multi-instance:
  * Anahtar tenant namespace'i taşır: `{tenant_id}:{message_id}`. Aynı mid
    farklı tenant'larda çakışmaz.
  * REDIS_URL tanımlıysa Redis'te `SET NX EX` ile atomik ve TÜM instance'lar
    arası paylaşımlı çalışır. Yoksa süreç-içi (namespaced) belleğe düşer —
    tek instance için doğru, ölçeklemede Redis önerilir.
"""

import time

from config import PROCESSED_MESSAGE_TTL, REDIS_URL

# Süreç-içi yedek (Redis yoksa). Anahtar: '{tenant}:{mid}' -> created_at.
processed_messages = {}

_redis_client = None
_redis_ready = False


def _get_redis():
    global _redis_client, _redis_ready
    if _redis_ready:
        return _redis_client
    _redis_ready = True
    if not REDIS_URL:
        _redis_client = None
        return None
    try:
        import redis

        client = redis.Redis.from_url(
            REDIS_URL, decode_responses=True,
            socket_connect_timeout=3, socket_timeout=3,
        )
        client.ping()
        _redis_client = client
    except Exception as e:
        print(f"⚠️ dedup Redis'e bağlanılamadı ({e}) — bellek yedeğine düşülüyor.")
        _redis_client = None
    return _redis_client


def _ns_key(message_id):
    from Services.db import get_current_tenant
    from Services.models import DEFAULT_TENANT_ID

    tenant = get_current_tenant()
    if tenant is None:
        tenant = DEFAULT_TENANT_ID
    return f"{tenant}:{message_id}"


def is_duplicate(message_id):
    """message_id daha önce (aktif tenant kapsamında) işlendiyse True.

    İlk görülüşte kaydı oluşturur ve False döner. Redis varsa atomik SET NX ile.
    """
    ns_key = _ns_key(message_id)

    client = _get_redis()
    if client is not None:
        try:
            # NX: yalnız yoksa yaz. Yazabildiysek (was_set=True) → ilk kez → duplicate değil.
            was_set = client.set(
                f"ia:dedup:{ns_key}", "1", nx=True, ex=PROCESSED_MESSAGE_TTL
            )
            return not was_set
        except Exception as e:
            print(f"⚠️ dedup Redis hatası ({e}) — bellek yedeğine düşülüyor.")

    # Süreç-içi yedek (namespaced)
    now = time.time()

    expired = [
        k for k, created_at in processed_messages.items()
        if now - created_at > PROCESSED_MESSAGE_TTL
    ]
    for k in expired:
        del processed_messages[k]

    if ns_key in processed_messages:
        return True

    processed_messages[ns_key] = now
    return False
