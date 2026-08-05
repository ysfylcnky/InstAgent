"""Panel girişi TENANT'A AİT olmalı — .env'e yazılmamalı.

Kurulum sihirbazının "advanced" bölümü eskiden DASHBOARD_USER/DASHBOARD_PASSWORD'ü
ortak `.env` dosyasına yazıyordu; çok kiracılı üründe ikinci tenant birincinin
giriş bilgisini EZİYORDU. Artık `users` tablosuna yazılır (target="account").

Kanıtlananlar:
  * save_section("advanced", ...) aktif tenant'a owner kullanıcı açar.
  * İki tenant birbirinin giriş bilgisini ezmez.
  * Parola düz metin saklanmaz; doğru parola ile authenticate edilir.
  * Boş parola gönderilirse mevcut parola korunur (email güncellenebilir).
  * Başka tenant'ta kayıtlı email reddedilir.
  * Panel bilgileri .env yazımına DÜŞMEZ (restart_required tetiklenmez).
"""

from sqlalchemy import select

from Services.db import get_session, tenant_scope
from Services.models import User
from Services import setup_service, user_service
from Services.auth_service import authenticate
from conftest import TENANT_A, TENANT_B


def _owner(tenant_id):
    with get_session(scoped=False) as s:
        return s.execute(
            select(User).where(User.tenant_id == tenant_id, User.role == "owner")
        ).scalars().first()


def test_advanced_section_creates_tenant_owner(env):
    with tenant_scope(TENANT_A):
        res = setup_service.save_section("advanced", {
            "PANEL_EMAIL": "A@Magaza.com",
            "PANEL_PASSWORD": "parolaA123",
        })

    assert res["ok"] is True, res
    # .env yazımı olmadığı için yeniden başlatma gerekmez
    assert res["restart_required"] is False

    owner = _owner(TENANT_A)
    assert owner is not None
    assert owner.email == "a@magaza.com"          # normalize edilir
    assert owner.password_hash != "parolaA123"    # düz metin saklanmaz

    ctx = authenticate("a@magaza.com", "parolaA123")
    assert ctx is not None
    assert ctx["tenant_id"] == TENANT_A
    assert ctx["role"] == "owner"


def test_two_tenants_do_not_overwrite_each_other(env):
    with tenant_scope(TENANT_A):
        setup_service.save_section("advanced", {
            "PANEL_EMAIL": "a@magaza.com", "PANEL_PASSWORD": "parolaA123",
        })
    with tenant_scope(TENANT_B):
        setup_service.save_section("advanced", {
            "PANEL_EMAIL": "b@magaza.com", "PANEL_PASSWORD": "parolaB123",
        })

    assert _owner(TENANT_A).email == "a@magaza.com"
    assert _owner(TENANT_B).email == "b@magaza.com"

    # Her tenant kendi kimliğiyle girer; çapraz parola çalışmaz.
    assert authenticate("a@magaza.com", "parolaA123")["tenant_id"] == TENANT_A
    assert authenticate("b@magaza.com", "parolaB123")["tenant_id"] == TENANT_B
    assert authenticate("a@magaza.com", "parolaB123") is None


def test_blank_password_keeps_existing(env):
    with tenant_scope(TENANT_A):
        setup_service.save_section("advanced", {
            "PANEL_EMAIL": "a@magaza.com", "PANEL_PASSWORD": "parolaA123",
        })
        res = setup_service.save_section("advanced", {
            "PANEL_EMAIL": "yeni@magaza.com", "PANEL_PASSWORD": "",
        })

    assert res["ok"] is True, res
    assert _owner(TENANT_A).email == "yeni@magaza.com"
    # Parola değişmedi
    assert authenticate("yeni@magaza.com", "parolaA123") is not None


def test_email_taken_by_other_tenant_is_rejected(env):
    with tenant_scope(TENANT_A):
        setup_service.save_section("advanced", {
            "PANEL_EMAIL": "ortak@magaza.com", "PANEL_PASSWORD": "parolaA123",
        })
    with tenant_scope(TENANT_B):
        res = setup_service.save_section("advanced", {
            "PANEL_EMAIL": "ortak@magaza.com", "PANEL_PASSWORD": "parolaB123",
        })

    assert res["ok"] is False
    assert _owner(TENANT_B) is None
    # A'nın kaydı bozulmadı
    assert authenticate("ortak@magaza.com", "parolaA123")["tenant_id"] == TENANT_A


def test_invalid_email_rejected(env):
    with tenant_scope(TENANT_A):
        res = setup_service.save_section("advanced", {
            "PANEL_EMAIL": "bu-bir-email-degil", "PANEL_PASSWORD": "parolaA123",
        })
    assert res["ok"] is False
    assert _owner(TENANT_A) is None


def test_short_password_rejected(env):
    with tenant_scope(TENANT_A):
        res = setup_service.save_section("advanced", {
            "PANEL_EMAIL": "a@magaza.com", "PANEL_PASSWORD": "kisa",
        })
    assert res["ok"] is False
    assert _owner(TENANT_A) is None


def test_setup_state_shows_email_not_password(env):
    with tenant_scope(TENANT_A):
        setup_service.save_section("advanced", {
            "PANEL_EMAIL": "a@magaza.com", "PANEL_PASSWORD": "parolaA123",
        })
        state = setup_service.get_setup_state()

    adv = next(s for s in state["sections"] if s["id"] == "advanced")
    fields = {f["key"]: f for f in adv["fields"]}

    assert fields["PANEL_EMAIL"]["value"] == "a@magaza.com"
    assert fields["PANEL_EMAIL"]["set"] is True
    # Parola asla geri gönderilmez
    assert fields["PANEL_PASSWORD"]["value"] is None


def test_upsert_requires_both_on_first_create(env):
    # Owner yokken tek başına email → hata (parola olmadan hesap açılamaz)
    with tenant_scope(TENANT_A):
        res = setup_service.save_section("advanced", {"PANEL_EMAIL": "a@magaza.com"})
    assert res["ok"] is False
    assert _owner(TENANT_A) is None


def test_get_tenant_owner_isolated(env):
    with tenant_scope(TENANT_A):
        setup_service.save_section("advanced", {
            "PANEL_EMAIL": "a@magaza.com", "PANEL_PASSWORD": "parolaA123",
        })

    assert user_service.get_tenant_owner(TENANT_A).email == "a@magaza.com"
    assert user_service.get_tenant_owner(TENANT_B) is None
