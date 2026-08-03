"""GDPR / Meta App Review — Veri Silme + Deauthorize iş mantığı.

Meta App Review iki zorunlu callback ister:

  * Data Deletion Request → bir kullanıcı (IGSID) verisinin silinmesi. Kullanıcı
    uygulama içinden "verimi sil" derse Meta buraya `signed_request` POST eder.
    Silinen tablolar: conversations, orders, customers, usage_logs (hepsinde
    IGSID sırasıyla sender / customer_phone / phone / sender alanında).

  * Deauthorize → kullanıcı uygulamayı kaldırınca ilgili tenant'ın Instagram
    bağlantısı pasifleştirilir (token temizlenir + tenants.status=inactive).

İmza doğrulaması `Services/meta_verify.parse_signed_request` ile yapılır (bu
modül gövdenin zaten doğrulanmış payload'ıyla çağrılır). Sır asla loglanmaz.

Silme çapraz-tenant PLATFORM işidir: bir IGSID teorik olarak birden çok tenant'a
(farklı mağazalara mesaj atmış) ait olabilir; kullanıcının silme talebi verisini
HER YERDEN kaldırır. Bu yüzden `scoped=False` kullanılır (tenant filtresi bypass)
ve silme yalnız o IGSID'e ait satırlara uygulanır — başka kullanıcı etkilenmez.
"""

import secrets

from sqlalchemy import select

from Services.db import get_session, tenant_scope
from Services.models import (
    Conversation,
    Customer,
    Order,
    UsageLog,
    Tenant,
)
from Services import settings_service, tenant_service


def delete_customer_data(igsid):
    """Verilen IGSID'e ait müşteri verisini TÜM tenant'lardan siler.

    Döner: {"conversations": n, "orders": n, "customers": n, "usage_logs": n}.
    Yalnız bu IGSID'e ait satırlar silinir; başka kullanıcı/veri etkilenmez.
    """
    empty = {"conversations": 0, "orders": 0, "customers": 0, "usage_logs": 0}

    igsid = "" if igsid is None else str(igsid).strip()
    if not igsid:
        return empty

    # Cross-tenant platform silme → scoped=False (tenant filtresi bilinçli bypass).
    # Bulk DELETE tenant filtresinden geçmez (yalnız SELECT filtrelenir); bu yüzden
    # WHERE koşulu IGSID'e daraltılarak yanlışlıkla başka satır silinmez.
    with get_session(scoped=False) as s:
        deleted = {
            "conversations": s.query(Conversation)
            .filter(Conversation.sender == igsid)
            .delete(synchronize_session=False),
            "orders": s.query(Order)
            .filter(Order.customer_phone == igsid)
            .delete(synchronize_session=False),
            "customers": s.query(Customer)
            .filter(Customer.phone == igsid)
            .delete(synchronize_session=False),
            "usage_logs": s.query(UsageLog)
            .filter(UsageLog.sender == igsid)
            .delete(synchronize_session=False),
        }

    return deleted


def handle_data_deletion(igsid):
    """Silmeyi çalıştırır ve (confirmation_code, deleted_counts) döner.

    confirmation_code kullanıcının silme durumunu takip edebilmesi için üretilen
    tahmin edilemez bir koddur (Meta yanıtında zorunlu alan).
    """
    deleted = delete_customer_data(igsid)
    confirmation_code = secrets.token_hex(8)
    return confirmation_code, deleted


def deauthorize_tenant(ig_account_id):
    """IG hesap kimliğine bağlı tenant'ın Instagram bağlantısını pasifleştirir.

    - tenants.status = "inactive" (resolver artık bu hesabı çözmez → webhook'lar
      fail-closed reddedilir).
    - IG_ACCESS_TOKEN ayarı temizlenir (bağlantı sırrı kalmaz).
    - tenant resolver cache'i temizlenir.

    Not: Deauthorize `signed_request`'indeki `user_id`, bağlı Instagram hesabının
    kimliği olarak yorumlanır (tenant anahtarımız = IG Business Account ID). Meta
    payload'u farklı bir kimlik döndürürse eşleşme bulunmaz ve fail-safe olarak
    hiçbir tenant etkilenmeden ok dönülür.

    Döner: {"deactivated": bool, "tenant_id": int|None}.
    """
    ig_account_id = "" if ig_account_id is None else str(ig_account_id).strip()
    if not ig_account_id:
        return {"deactivated": False, "tenant_id": None}

    tenant_id = None
    with get_session(scoped=False) as s:
        tenant = s.execute(
            select(Tenant).where(Tenant.ig_account_id == ig_account_id)
        ).scalar_one_or_none()
        if tenant is None:
            return {"deactivated": False, "tenant_id": None}
        tenant_id = tenant.id
        tenant.status = "inactive"

    # Bağlantı token'ını temizle (tenant kapsamında). Sır loglanmaz.
    with tenant_scope(tenant_id):
        settings_service.save_stored_settings({"IG_ACCESS_TOKEN": ""})

    # Hesap→tenant çözümleme cache'i eskimesin.
    tenant_service.invalidate(ig_account_id)

    return {"deactivated": True, "tenant_id": tenant_id}
