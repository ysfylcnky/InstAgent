"""ORM modelleri — multi-tenant SaaS şeması.

Tek-kiracılı şema, additive biçimde çok-kiracılıya taşındı:
  * Yeni kök (global) modeller: `Tenant`, `User`.
  * Tenant-owned tablolara `tenant_id` eklendi ve bunlar `TenantScoped`
    işaretini taşır — `Services/db.py` bu işareti kullanarak scoped session'da
    otomatik filtreleme (SELECT) ve otomatik damgalama (INSERT) yapar.

`TenantScoped` yalnızca bir İŞARET (marker) sınıfıdır; tenant_id sütunu her
modelde ayrı tanımlanır. Böylece `customers`/`settings` gibi tablolar tenant_id'yi
bileşik birincil anahtarın parçası yapabilir, diğerleri sıradan indeksli sütun
olarak tutabilir.

Kök modeller (`Tenant`, `User`) TenantScoped DEĞİLDİR: login ve tenant çözümü
bunlara filtre uygulanmadan erişebilmelidir.
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Double,
    Index,
    ForeignKey,
)
from sqlalchemy.dialects.mysql import TINYINT

from Services.db import Base


class TenantScoped:
    """İşaret sınıfı: bu sınıftan türeyen modeller tenant'a aittir.

    `Services/db.py` scoped session'ı, bu sınıfın alt tiplerine SELECT'te
    `WHERE tenant_id = <aktif tenant>` kriterini otomatik ekler ve yeni
    kayıtlara `tenant_id`'yi otomatik damgalar. Her model kendi `tenant_id`
    sütununu tanımlar (bileşik PK esnekliği için).
    """


# ----------------------------------------------------------------------
# Kök (global) modeller — tenant filtresine TABİ DEĞİL
# ----------------------------------------------------------------------

class Tenant(Base):
    """Kiracı (mağaza). SaaS'ın kök varlığı."""

    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    # Instagram webhook routing'in canonical anahtarı: IG Business Account ID
    # (webhook entry.id / recipient.id). Meta çapında globaldir → UNIQUE.
    ig_account_id = Column(String(64), nullable=True, unique=True, index=True)
    status = Column(String(32), nullable=False, default="active")
    # Billing hazırlığı (şimdilik kullanılmaz; şema genişletilebilir kalsın).
    plan = Column(String(32), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class User(Base):
    """Panel kullanıcısı. Bir tenant'a bağlıdır; email platform genelinde tekildir.

    Kök model olarak tutulur (TenantScoped değil): login sırasında email ile
    arama tenant bağlamı OLMADAN yapılmalıdır. Tenant kimliği kullanıcının
    kaydından (tenant_id) türetilir — request'ten gelen değere güvenilmez.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    # 'owner' (tenant sahibi) | 'member' | 'superadmin' (platform operatörü)
    role = Column(String(32), nullable=False, default="owner")
    created_at = Column(DateTime, nullable=False, default=datetime.now)


# ----------------------------------------------------------------------
# Tenant-owned modeller — scoped session tarafından otomatik izole edilir
# ----------------------------------------------------------------------

class UsageLog(Base, TenantScoped):
    """LLM istek maliyet/performans kaydı (usage_logs)."""

    __tablename__ = "usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    sender = Column(String(32), nullable=False)
    model = Column(String(64), nullable=False)
    prompt_tokens = Column(Integer, nullable=False)
    completion_tokens = Column(Integer, nullable=False)
    total_tokens = Column(Integer, nullable=False)
    cost = Column(Double, nullable=False)
    response_time = Column(Double, nullable=False)

    __table_args__ = (
        Index("idx_timestamp", "timestamp"),
        Index("idx_sender", "sender"),
        Index("idx_usage_tenant", "tenant_id"),
    )


class Conversation(Base, TenantScoped):
    """Instagram mesaj kaydı — gelen/giden (conversations)."""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    sender = Column(String(32), nullable=False)
    direction = Column(String(8), nullable=False)
    content = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_conv_sender", "sender"),
        Index("idx_conv_timestamp", "timestamp"),
        Index("idx_conv_tenant", "tenant_id"),
    )


class Customer(Base, TenantScoped):
    """Sipariş veren müşteri; Instagram IGSID birincil anahtar (customers).

    Multi-tenant: aynı IGSID teorik olarak farklı tenant'larda görülebileceğinden
    (ve çapraz erişimi engellemek için) birincil anahtar `(tenant_id, phone)`
    bileşiğidir.
    """

    __tablename__ = "customers"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), primary_key=True, nullable=False)
    phone = Column(String(32), primary_key=True)
    ad_soyad = Column(String(255), nullable=True)
    first_seen = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)


class Order(Base, TenantScoped):
    """Sipariş/güncelleme kaydı; güncelleme is_update=1 ile yeni satır (orders)."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    customer_phone = Column(String(32), nullable=False)
    ad_soyad = Column(String(255), nullable=True)
    telefon = Column(String(64), nullable=True)
    teslimat_adresi = Column(Text, nullable=True)
    urun = Column(String(255), nullable=True)
    renk = Column(String(128), nullable=True)
    beden = Column(String(128), nullable=True)
    adet = Column(Integer, nullable=True)
    odeme_sekli = Column(String(64), nullable=True)
    # MySQL'de TINYINT, diğer dialect'lerde (test: SQLite) INTEGER olarak derlenir.
    is_update = Column(
        Integer().with_variant(TINYINT(), "mysql"), nullable=False, default=0
    )

    __table_args__ = (
        Index("idx_orders_phone", "customer_phone"),
        Index("idx_orders_timestamp", "timestamp"),
        Index("idx_orders_tenant", "tenant_id"),
    )


class Setting(Base, TenantScoped):
    """Tenant'a özel anahtar-değer ayarı (settings).

    Panelden düzenlenen ayarlar (IBAN vb.), kurulum durumu (SETUP_*) ve
    tenant credential'ları burada tutulur. Secret anahtarlar (whitelist)
    `svalue` alanında Fernet ile ŞİFRELİ saklanır (settings_service uygular).
    Birincil anahtar `(tenant_id, skey)` bileşiğidir.
    """

    __tablename__ = "settings"

    tenant_id = Column(Integer, ForeignKey("tenants.id"), primary_key=True, nullable=False)
    skey = Column(String(64), primary_key=True)
    svalue = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False)


class OAuthState(Base):
    """Meta/Instagram OAuth `state` — CSRF + tenant bağlama (Faz 9).

    Kök model (tenant filtresine tabi değil). Her state tahmin edilemez, kısa
    ömürlü, tek kullanımlıktır ve bir tenant/user'a bağlıdır. Callback'te
    doğrulanıp SİLİNİR (single-use).
    """

    __tablename__ = "oauth_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state = Column(String(128), nullable=False, unique=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)


# Varsayılan (mevcut Mumi mağazası) tenant kimliği. Backfill ve tek-tenant
# köprüsü bu değeri kullanır. Faz 4'te gerçek webhook routing devreye girene
# kadar scope belirtilmemiş kod yolları bu tenant altında çalışır.
DEFAULT_TENANT_ID = 1


# Tenant izolasyonuna tabi somut modellerin ALLOWLIST'i. `Services/db.py` global
# filtreyi bu liste üzerinden uygular; yeni bir tenant-owned tablo eklenince
# BURAYA da eklenmelidir (aksi halde o tablo filtrelenmez → test yakalar).
TENANT_OWNED_MODELS = (UsageLog, Conversation, Customer, Order, Setting)
