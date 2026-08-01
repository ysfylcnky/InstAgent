"""SQLAlchemy veritabanı zemini + MERKEZÎ tenant izolasyonu.

Bu modül iki iş yapar:

1) Bağlantı/oturum altyapısı (mevcut MYSQL_* config; testlerde DATABASE_URL
   ile SQLite'a yönlendirilebilir).

2) Multi-tenant izolasyonun TEK merkezi. Tenant filtresi geliştiricinin her
   sorguya elle `WHERE tenant_id = ...` yazmasına bırakılmaz; session/ORM
   seviyesinde ZORUNLU kılınır:

   * SELECT  → `do_orm_execute` olayı, `TenantScoped` alt tiplerine aktif
     tenant kriterini otomatik ekler.
   * INSERT  → `before_flush` olayı, yeni `TenantScoped` kayıtlarına aktif
     tenant_id'yi otomatik damgalar ve çapraz-tenant insert'i reddeder.

   Aktif tenant `current_tenant_id` contextvar'ında tutulur; eşzamanlı
   webhook istekleri birbirinin tenant'ını görmez.

Kullanım:
    from Services.db import get_session, tenant_scope
    with tenant_scope(tenant_id):
        with get_session() as s:            # otomatik filtreli/damgalı
            s.add(Conversation(...))        # tenant_id otomatik
    # Cross-tenant sistem işleri (login, tenant resolution, migration):
    with get_session(scoped=False) as s:    # BİLİNÇLİ bypass
        ...
"""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from sqlalchemy import create_engine, event, false
from sqlalchemy.orm import sessionmaker, declarative_base, with_loader_criteria

# Tüm ORM modelleri bu Base'den türer (models.py bunu kullanır).
Base = declarative_base()


class TenantScopeError(Exception):
    """Tenant izolasyon ihlali — fail-closed sinyali (scope yok / çapraz insert)."""


# ----------------------------------------------------------------------
# Aktif tenant bağlamı
# ----------------------------------------------------------------------

# Aktif tenant kimliği. None ise (ve fallback açıksa) DEFAULT_TENANT_ID kullanılır.
current_tenant_id: ContextVar[Optional[int]] = ContextVar(
    "current_tenant_id", default=None
)

# Tek-tenant köprüsü: scope belirtilmemişse mevcut Mumi mağazasına (tenant 1)
# düş. Faz 1-3 geriye dönük uyumu içindir; Faz 10'da False'a çekilerek tam
# fail-closed davranışa geçilebilir. Webhook routing (Faz 4) bu köprüye
# GÜVENMEZ — bilinmeyen hesabı resolver reddeder.
_fallback_enabled = True


def set_default_tenant_fallback(enabled: bool):
    """Scope belirtilmemiş scoped session için tenant 1 köprüsünü aç/kapat."""
    global _fallback_enabled
    _fallback_enabled = bool(enabled)


def set_current_tenant(tenant_id):
    """Aktif tenant'ı ayarlar; önceki değeri geri almak için token döndürür."""
    return current_tenant_id.set(tenant_id)


def get_current_tenant():
    """Aktif tenant kimliğini döndürür (yoksa None)."""
    return current_tenant_id.get()


@contextmanager
def tenant_scope(tenant_id):
    """Blok boyunca aktif tenant'ı `tenant_id` yapar; çıkışta eski değere döner."""
    token = current_tenant_id.set(tenant_id)
    try:
        yield
    finally:
        current_tenant_id.reset(token)


def _resolve_scope_tenant():
    """Scoped session açılırken kullanılacak tenant kimliğini çözer."""
    from Services.models import DEFAULT_TENANT_ID

    tid = current_tenant_id.get()
    if tid is None and _fallback_enabled:
        return DEFAULT_TENANT_ID
    return tid


# ----------------------------------------------------------------------
# Engine / Session (lazy — testler DATABASE_URL'i import'tan sonra ayarlayabilsin)
# ----------------------------------------------------------------------

_engine = None
_SessionLocal = None


def _build_url():
    """Bağlantı URL'i: DATABASE_URL varsa onu, yoksa MYSQL_* config'ini kullanır."""
    override = os.getenv("DATABASE_URL")
    if override:
        return override

    from config import (
        MYSQL_HOST,
        MYSQL_PORT,
        MYSQL_USER,
        MYSQL_PASSWORD,
        MYSQL_DATABASE,
    )

    return (
        f"mysql+mysqlconnector://{MYSQL_USER}:{MYSQL_PASSWORD}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"
        "?charset=utf8mb4"
    )


def _create_engine():
    url = _build_url()

    if url.startswith("sqlite"):
        # Testlerde in-memory SQLite; tüm oturumlar aynı DB'yi paylaşsın.
        from sqlalchemy.pool import StaticPool

        connect_args = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            return create_engine(
                url,
                connect_args=connect_args,
                poolclass=StaticPool,
                future=True,
            )
        return create_engine(url, connect_args=connect_args, future=True)

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=5,
        max_overflow=5,
        future=True,
    )


def _get_sessionmaker():
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = _create_engine()
        _SessionLocal = sessionmaker(
            bind=_engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        _register_tenant_events(_SessionLocal)
    return _SessionLocal


def get_engine():
    """Aktif engine (lazy kurulur)."""
    _get_sessionmaker()
    return _engine


def reset_engine():
    """Test amaçlı: engine/sessionmaker'ı sıfırlar (DATABASE_URL değişince)."""
    global _engine, _SessionLocal
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None
    _SessionLocal = None


# ----------------------------------------------------------------------
# Tenant izolasyon olayları (SELECT filtre + INSERT damga)
# ----------------------------------------------------------------------

def _register_tenant_events(session_factory):
    """Verilen sessionmaker'a tenant izolasyon olay dinleyicilerini bağlar."""

    @event.listens_for(session_factory, "do_orm_execute")
    def _apply_tenant_filter(execute_state):
        # Yalnız ORM SELECT'lerini filtrele. Yazma/güncelleme/silme buradan geçmez.
        if not execute_state.is_select:
            return
        sess = execute_state.session
        if not sess.info.get("scoped", False):
            return

        from Services.models import TENANT_OWNED_MODELS

        tid = sess.info.get("tenant_id")

        # Filtre, somut modeller üzerinden (allowlist) uygulanır. Sorguda yer
        # almayan model için option no-op'tur; sızıntı olması için bir tablonun
        # bu listede OLMAMASI gerekir — bunu testler yakalar.
        #
        # Kriter DOĞRUDAN ifade olarak verilir (lambda DEĞİL): lambda tabanlı
        # with_loader_criteria, closure/argümanları önbelleğe alıp aynı derlenmiş
        # sorguyu farklı tenant_id ile tekrar kullanabilir (izolasyon açığı).
        # Doğrudan ifade her istekte tenant_id'yi taze bağlar.
        options = []
        for model in TENANT_OWNED_MODELS:
            # Scoped ama tenant yok → fail-closed: hiçbir tenant-owned satır dönmez.
            crit = false() if tid is None else (model.tenant_id == tid)
            options.append(with_loader_criteria(model, crit, include_aliases=True))

        execute_state.statement = execute_state.statement.options(*options)

    @event.listens_for(session_factory, "before_flush")
    def _stamp_tenant_on_insert(sess, flush_context, instances):
        if not sess.info.get("scoped", False):
            return

        from Services.models import TenantScoped

        tid = sess.info.get("tenant_id")

        for obj in sess.new:
            if not isinstance(obj, TenantScoped):
                continue
            current = getattr(obj, "tenant_id", None)
            if current is None:
                if tid is None:
                    raise TenantScopeError(
                        "Scoped session'da aktif tenant yok — tenant-owned kayıt "
                        "eklenemez (fail-closed)."
                    )
                obj.tenant_id = tid
            elif tid is not None and current != tid:
                raise TenantScopeError(
                    "Aktif tenant ile kaydın tenant_id'si uyuşmuyor — çapraz "
                    "tenant insert reddedildi."
                )

        # Güncellemede tenant_id'nin başka tenant'a taşınmasını da engelle.
        for obj in sess.dirty:
            if not isinstance(obj, TenantScoped):
                continue
            if not sess.is_modified(obj, include_collections=False):
                continue
            current = getattr(obj, "tenant_id", None)
            if tid is not None and current is not None and current != tid:
                raise TenantScopeError(
                    "Bir kaydın tenant_id'si aktif tenant dışına taşınamaz."
                )


# ----------------------------------------------------------------------
# Oturum context'i
# ----------------------------------------------------------------------

@contextmanager
def get_session(scoped=True):
    """İşlem sınırı sağlayan oturum context'i.

    scoped=True (varsayılan): aktif tenant'a göre otomatik filtre + damga.
    scoped=False: BİLİNÇLİ bypass — yalnız gerçek cross-tenant işlerde
    (login, tenant resolution, onboarding, migration, platform yönetimi).
    """
    SessionLocal = _get_sessionmaker()
    session = SessionLocal()
    session.info["scoped"] = scoped
    if scoped:
        session.info["tenant_id"] = _resolve_scope_tenant()

    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
