"""Tenant'a özel anahtar-değer ayarları (settings tablosu) — ORM + şifreleme.

Faz 3: bu servis artık ham SQL yerine SQLAlchemy ORM (`Setting` modeli) ve
scoped session kullanır. Böylece okuma/yazma OTOMATİK olarak aktif tenant'a
izole olur — burada elle `WHERE tenant_id = ...` yazılmaz.

Secret yönetimi:
  * `SECRET_SETTING_KEYS` whitelist'indeki anahtarların değeri DB'ye Fernet ile
    ŞİFRELİ yazılır (`crypto_service.encrypt`) ve okurken çözülür.
  * `get_all_stored_settings()` ham (secret'lar ŞİFRELİ) değerleri döndürür —
    setup ekranı zaten secret'ları maskeler; toplu okuma sır çözmez/sızdırmaz.
  * `get_stored_setting(key)` tekil okumada secret ise ÇÖZER (config ve tenant
    credential accessor'ları bunu kullanır).

config.py bu servisi öncelikli kaynak olarak okur; kayıt yoksa .env / kod
varsayılanına düşülür. Okuma DB erişilemezse uygulamayı çökertmez.
"""

from datetime import datetime

from sqlalchemy import select

from Services.db import get_session
from Services.models import Setting
from Services import crypto_service


# Değeri DB'de ŞİFRELİ tutulacak tenant sırları. Bu whitelist dışındaki her
# anahtar düz metin ayardır. Sistem sırları (JWT_SECRET, ENCRYPTION_KEY, MySQL)
# BURADA DEĞİLDİR — onlar .env/sistem config'inde kalır, tenant_settings'e girmez.
SECRET_SETTING_KEYS = frozenset({
    "IG_ACCESS_TOKEN",
    "IKAS_CLIENT_SECRET",
    "OPENAI_API_KEY",
    "WHATSAPP_ACCESS_TOKEN",
})


def is_secret_key(key):
    return key in SECRET_SETTING_KEYS


def get_all_stored_settings():
    """Aktif tenant'ın tüm ayarlarını {skey: svalue} döndürür.

    Secret anahtarların değeri ŞİFRELİ (ham) döner — toplu okumada sır çözülmez.
    Tekil sır için get_stored_setting kullanın. DB hatası -> {} (fail-open okuma).
    """
    try:
        with get_session() as s:
            rows = s.execute(select(Setting.skey, Setting.svalue)).all()
            return {k: v for k, v in rows}
    except Exception as e:
        print("🔴 get_all_stored_settings hatası:", e)
        return {}


def get_stored_setting(key):
    """Aktif tenant için tek ayarın değerini döndürür (secret ise çözülmüş).

    Yoksa/erişilemezse None. Secret çözme başarısızsa (bozuk anahtar) hata
    yutulur ve None döner — düz metin ham blob ASLA döndürülmez (fail-closed).
    """
    try:
        with get_session() as s:
            row = s.execute(
                select(Setting.svalue).where(Setting.skey == key)
            ).first()
    except Exception as e:
        print("🔴 get_stored_setting hatası:", e)
        return None

    if not row:
        return None

    value = row[0]

    if is_secret_key(key):
        try:
            return crypto_service.decrypt(value)
        except crypto_service.CryptoError as e:
            print(f"🔴 sır çözülemedi ({key}):", e)
            return None

    return value


def save_stored_settings(mapping):
    """Verilen {skey: svalue} eşlemesini aktif tenant için UPSERT eder.

    Secret anahtarların değeri yazmadan önce şifrelenir. Boş string değer,
    ilgili ayarın .env/varsayılana düşmesi anlamına gelir. Başarıda True.
    """
    if not mapping:
        return True

    try:
        with get_session() as s:
            now = datetime.now()

            for skey, svalue in mapping.items():
                store_value = svalue
                if is_secret_key(skey) and svalue not in (None, ""):
                    store_value = crypto_service.encrypt(svalue)

                # Scoped session sayesinde bu sorgu yalnız aktif tenant'ın satırını
                # görür; varsa günceller, yoksa ekler (tenant_id otomatik damgalanır).
                existing = s.execute(
                    select(Setting).where(Setting.skey == skey)
                ).scalar_one_or_none()

                if existing is not None:
                    existing.svalue = store_value
                    existing.updated_at = now
                else:
                    s.add(Setting(skey=skey, svalue=store_value, updated_at=now))

        return True
    except Exception as e:
        print("🔴 save_stored_settings hatası:", e)
        return False
