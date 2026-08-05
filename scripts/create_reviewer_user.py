"""Meta App Review reviewer'ı için mevcut bir tenant'a panel kullanıcısı ekler.

Neden ayrı script: `POST /admin/platform/tenants` YENİ tenant açar. Reviewer'ın
zaten Instagram bağlı ve çalışır durumdaki tenant'a girmesi gerekiyor, o yüzden
var olan tenant'a `member` rolünde bir kullanıcı ekliyoruz.

Kullanım (sunucuda, repo kökünde):

    docker compose exec app python -m scripts.create_reviewer_user --list
    docker compose exec app python -m scripts.create_reviewer_user \
        --tenant-id 1 --email metareview@mumifashion.com --password 'GucluBirSifre123'

App Review bittikten sonra kullanıcıyı sil:

    docker compose exec app python -m scripts.create_reviewer_user \
        --delete --email metareview@mumifashion.com
"""

import argparse
import sys

from sqlalchemy import select

from Services.db import get_session
from Services.models import Tenant, User
from Services.auth_service import hash_password


def list_tenants():
    with get_session(scoped=False) as s:
        tenants = s.execute(select(Tenant).order_by(Tenant.id)).scalars().all()
        rows = [(t.id, t.name, t.ig_account_id, t.status) for t in tenants]
        users = s.execute(select(User).order_by(User.tenant_id, User.id)).scalars().all()
        urows = [(u.id, u.tenant_id, u.email, u.role) for u in users]

    print("\nTENANT'LAR")
    print(f"{'id':<5}{'ad':<28}{'ig_account_id':<22}{'durum'}")
    for tid, name, ig, status in rows:
        print(f"{tid:<5}{(name or '')[:26]:<28}{(ig or '—'):<22}{status}")

    print("\nKULLANICILAR")
    print(f"{'id':<5}{'tenant':<8}{'email':<38}{'rol'}")
    for uid, tid, email, role in urows:
        print(f"{uid:<5}{tid:<8}{email[:36]:<38}{role}")
    print()
    print("Reviewer'a Instagram hesabı BAĞLI olan tenant'ı ver "
          "(ig_account_id sütunu dolu olan).")


def create_user(tenant_id, email, password, role):
    email = (email or "").strip().lower()

    if not email or "@" not in email:
        sys.exit("Geçerli bir email gerekli.")
    if not password or len(password) < 8:
        sys.exit("Parola en az 8 karakter olmalı.")

    with get_session(scoped=False) as s:
        tenant = s.get(Tenant, tenant_id)
        if tenant is None:
            sys.exit(f"Tenant {tenant_id} bulunamadı. Önce --list ile bak.")

        if s.execute(select(User).where(User.email == email)).scalar_one_or_none():
            sys.exit(f"'{email}' zaten kayıtlı. Başka bir email seç veya --delete ile sil.")

        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            role=role,
        )
        s.add(user)
        s.flush()
        uid, tname, ig = user.id, tenant.name, tenant.ig_account_id

    ig_text = ig if ig else "YOK — bu tenant'a Instagram bağlı DEĞİL!"

    print(f"\n✅ Kullanıcı oluşturuldu (id={uid})")
    print(f"   tenant  : {tenant_id} — {tname}")
    print(f"   ig hesap: {ig_text}")
    print(f"   email   : {email}")
    print(f"   rol     : {role}")
    print("\nMeta submission → Credentials alanına bu email + parolayı gir.")
    print("Giriş: https://ig.mumifashion.com/login\n")


def delete_user(email):
    email = (email or "").strip().lower()
    with get_session(scoped=False) as s:
        user = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            sys.exit(f"'{email}' bulunamadı.")
        if user.role == "owner":
            sys.exit("Bu kullanıcı tenant sahibi (owner) — silmeyi reddediyorum.")
        s.delete(user)
    print(f"🗑️  '{email}' silindi.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--list", action="store_true", help="Tenant ve kullanıcıları listele")
    p.add_argument("--tenant-id", type=int, help="Kullanıcının ekleneceği tenant")
    p.add_argument("--email")
    p.add_argument("--password")
    p.add_argument("--role", default="member", choices=["member", "owner"])
    p.add_argument("--delete", action="store_true", help="--email ile verilen kullanıcıyı sil")
    a = p.parse_args()

    if a.list:
        return list_tenants()
    if a.delete:
        return delete_user(a.email)
    if not (a.tenant_id and a.email and a.password):
        p.error("--tenant-id, --email ve --password gerekli (ya da --list / --delete).")
    create_user(a.tenant_id, a.email, a.password, a.role)


if __name__ == "__main__":
    main()
