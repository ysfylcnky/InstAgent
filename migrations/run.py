"""Multi-tenant şema migration'ı — additive ve idempotent.

İki kurulum senaryosunu da güvenle karşılar:

  * TEMİZ kurulum: `Base.metadata.create_all` yeni şemayı (tenants, users +
    tenant_id'li tablolar) doğrudan kurar.
  * MEVCUT tek-tenant kurulum (Mumi): mevcut tablolara `tenant_id` eklenir,
    tüm satırlar DEFAULT_TENANT_ID (1) ile backfill edilir; ardından
    customers/settings için bileşik anahtar uygulanır.

Adım sırası (roadmap Faz 12 — DB hardening ile uyumlu):
  1) tenant_id NULLABLE ekle (henüz NOT NULL yapma)
  2) default tenant oluştur
  3) mevcut kayıtları backfill et (tenant_id=1)
  4) (kod tenant-aware çalışır)
  5) izolasyon testleri
  6) DOĞRULANDIKTAN sonra NOT NULL + bileşik anahtar (Faz 10 migration'ı)

Kullanım (production, konteyner içi):
    python -m migrations.run apply
    python -m migrations.run apply --tenant-name "Mumi" --ig-account-id 178...

MySQL'e özgü ALTER'lar yalnızca MySQL dialect'inde çalışır; SQLite (test) için
create_all zaten hedef şemayı kurduğundan ALTER adımları atlanır.
"""

import argparse
import os
import sys

from sqlalchemy import inspect, text

# Proje kökünü path'e ekle (python -m migrations.run ile de çalışsın)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Services.db import get_engine  # noqa: E402
from Services.models import Base, DEFAULT_TENANT_ID  # noqa: E402


# Mevcut tek-tenant tablolara eklenecek tenant_id sütunu (nullable — additive).
_ADD_TENANT_ID = {
    "usage_logs": "ALTER TABLE usage_logs ADD COLUMN tenant_id INT NULL, "
                  "ADD INDEX idx_usage_tenant (tenant_id)",
    "conversations": "ALTER TABLE conversations ADD COLUMN tenant_id INT NULL, "
                     "ADD INDEX idx_conv_tenant (tenant_id)",
    "orders": "ALTER TABLE orders ADD COLUMN tenant_id INT NULL, "
              "ADD INDEX idx_orders_tenant (tenant_id)",
    "customers": "ALTER TABLE customers ADD COLUMN tenant_id INT NULL",
    "settings": "ALTER TABLE settings ADD COLUMN tenant_id INT NULL",
}


def _has_column(inspector, table, column):
    try:
        cols = {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return False
    return column in cols


def _mysql_add_tenant_columns(conn, inspector):
    """Mevcut tablolara tenant_id ekler (yoksa). MySQL'e özgü."""
    for table, ddl in _ADD_TENANT_ID.items():
        if table not in inspector.get_table_names():
            continue  # tablo yoksa create_all kurmuştur (tenant_id'li)
        if _has_column(inspector, table, "tenant_id"):
            continue
        print(f"  + {table}.tenant_id ekleniyor")
        conn.execute(text(ddl))


def _backfill(conn, inspector):
    """Mevcut satırların tenant_id'sini DEFAULT_TENANT_ID ile doldurur."""
    for table in _ADD_TENANT_ID:
        if table not in inspector.get_table_names():
            continue
        if not _has_column(inspector, table, "tenant_id"):
            continue
        res = conn.execute(
            text(f"UPDATE {table} SET tenant_id = :tid WHERE tenant_id IS NULL"),
            {"tid": DEFAULT_TENANT_ID},
        )
        if res.rowcount:
            print(f"  ~ {table}: {res.rowcount} satır tenant {DEFAULT_TENANT_ID}'e backfill edildi")


def _ensure_default_tenant(conn, name, ig_account_id):
    """Default tenant (id=1) yoksa oluşturur; ig_account_id verilmişse yazar."""
    row = conn.execute(
        text("SELECT id, ig_account_id FROM tenants WHERE id = :id"),
        {"id": DEFAULT_TENANT_ID},
    ).fetchone()

    if row is None:
        from datetime import datetime

        conn.execute(
            text("INSERT INTO tenants (id, name, ig_account_id, status, created_at) "
                 "VALUES (:id, :name, :ig, 'active', :created)"),
            {"id": DEFAULT_TENANT_ID, "name": name, "ig": ig_account_id or None,
             "created": datetime.now()},
        )
        print(f"  + default tenant (id={DEFAULT_TENANT_ID}, name={name!r}) oluşturuldu")
    elif ig_account_id and not row[1]:
        conn.execute(
            text("UPDATE tenants SET ig_account_id = :ig WHERE id = :id"),
            {"ig": ig_account_id, "id": DEFAULT_TENANT_ID},
        )
        print(f"  ~ default tenant ig_account_id güncellendi")


def apply(tenant_name="Mumi", ig_account_id=None):
    engine = get_engine()
    dialect = engine.dialect.name
    print(f"Migration başlıyor (dialect={dialect}) …")

    # 1) Eksik tabloları/tam şemayı kur (idempotent; mevcut tabloları DEĞİŞTİRMEZ).
    Base.metadata.create_all(engine)
    print("  create_all tamam (eksik tablolar kuruldu)")

    with engine.begin() as conn:
        inspector = inspect(conn)

        # 2) MySQL: mevcut tablolara tenant_id ekle (SQLite'ta create_all zaten kurdu)
        if dialect == "mysql":
            _mysql_add_tenant_columns(conn, inspect(conn))

        # 3) default tenant + backfill
        ig = ig_account_id or os.getenv("IG_ACCOUNT_ID")
        _ensure_default_tenant(conn, tenant_name, ig)
        _backfill(conn, inspect(conn))

    print("Migration tamam.")
    print("NOT: tenant_id NOT NULL ve customers/settings bileşik anahtarı Faz 10 "
          "(0004_hardening) migration'ında, izolasyon doğrulandıktan SONRA uygulanır.")


# ----------------------------------------------------------------------
# Faz 10 — DB HARDENING (yalnız izolasyon DOĞRULANDIKTAN sonra çalıştırılır)
# ----------------------------------------------------------------------

# tenant_id'yi NOT NULL yapan ALTER'lar (backfill sonrası).
_NOT_NULL = [
    "ALTER TABLE usage_logs   MODIFY tenant_id INT NOT NULL",
    "ALTER TABLE conversations MODIFY tenant_id INT NOT NULL",
    "ALTER TABLE orders       MODIFY tenant_id INT NOT NULL",
    "ALTER TABLE customers    MODIFY tenant_id INT NOT NULL",
    "ALTER TABLE settings     MODIFY tenant_id INT NOT NULL",
]

# Bileşik anahtarlar: aynı IGSID/skey farklı tenant'larda çakışmasın.
# (customers/settings eski tek-sütun PK'sını bileşiğe çevirir.)
_COMPOSITE_KEYS = [
    "ALTER TABLE customers DROP PRIMARY KEY, ADD PRIMARY KEY (tenant_id, phone)",
    "ALTER TABLE settings  DROP PRIMARY KEY, ADD PRIMARY KEY (tenant_id, skey)",
]


def harden():
    """tenant_id NOT NULL + customers/settings bileşik anahtar (MySQL).

    ÖN KOŞUL: apply() çalıştırıldı, tüm satırlar backfill edildi ve izolasyon
    testleri geçti. SQLite (test) için create_all zaten hedef şemayı kurar;
    bu adım yalnız MySQL üretiminde gereklidir.
    """
    engine = get_engine()
    if engine.dialect.name != "mysql":
        print("harden yalnız MySQL'de gereklidir (SQLite'ta create_all yeterli). Atlanıyor.")
        return

    with engine.begin() as conn:
        # NULL kalan tenant_id var mı? Varsa hardening'i durdur (fail-safe).
        for table in _ADD_TENANT_ID:
            n = conn.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id IS NULL")
            ).scalar()
            if n:
                raise RuntimeError(
                    f"{table}: {n} satırda tenant_id NULL — önce apply/backfill çalıştırın."
                )
        for ddl in _NOT_NULL:
            print("  " + ddl)
            conn.execute(text(ddl))
        for ddl in _COMPOSITE_KEYS:
            print("  " + ddl)
            conn.execute(text(ddl))
    print("Hardening tamam.")


def main():
    ap = argparse.ArgumentParser(description="InstaAgent multi-tenant migration")
    ap.add_argument("command", choices=["apply", "harden"], help="uygulanacak komut")
    ap.add_argument("--tenant-name", default="Mumi", help="default tenant adı")
    ap.add_argument("--ig-account-id", default=None,
                    help="default tenant'ın IG Business Account ID'si")
    args = ap.parse_args()

    if args.command == "apply":
        apply(tenant_name=args.tenant_name, ig_account_id=args.ig_account_id)
    elif args.command == "harden":
        harden()


if __name__ == "__main__":
    main()
