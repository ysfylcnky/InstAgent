"""Tenant sırlarının simetrik şifrelenmesi — Fernet (AES-128-CBC + HMAC).

Multi-tenant SaaS'ta her tenant kendi Instagram/İKAS/OpenAI credential'larını
saklar. Bu değerler DB'de ASLA düz metin tutulmaz: `tenant_settings` tablosunda
Fernet ile şifreli yazılır, okurken çözülür (transparent encrypt/decrypt).

Tasarım kuralları:
  * SİSTEM seviyesinde tek master anahtar: ortam değişkeni ENCRYPTION_KEY
    (urlsafe base64, 32 bayt — `Fernet.generate_key()` çıktısı). Bu anahtar
    tenant'a değil platforma aittir; .env / secret manager'da tutulur.
  * Fail-closed: anahtar tanımlı değilse ya da bir token çözülemezse istisna
    YÜKSELTİLİR — sessizce düz metne düşülmez. Böylece bozuk/eksik anahtar
    fark edilmeden sır sızdırmaz.
  * Whitelist: yalnızca `SECRET_SETTING_KEYS` içindeki ayar anahtarları
    şifrelenir; gerisi düz metin ayardır (settings_service bunu uygular).
  * Şifreli token'lar `enc:v1:` ön-ekiyle işaretlenir; böylece bir değerin
    şifreli mi düz metin mi olduğu (ör. geçiş dönemi) güvenle ayırt edilir.
"""

import os
from functools import lru_cache

# Şifreli değerlerin başına konan sürüm etiketi. İleride anahtar rotasyonu
# gerekirse v2 eklenip eski token'lar tanınmaya devam edebilir.
ENC_PREFIX = "enc:v1:"


class CryptoError(Exception):
    """Şifreleme/çözme başarısız — fail-closed sinyali."""


def _load_key():
    """ENCRYPTION_KEY ortam değişkeninden Fernet anahtarını yükler.

    Anahtar okuma import anında DEĞİL, ilk kullanımda yapılır; böylece sır
    gerektirmeyen kod yolları anahtar tanımlı olmadan da import edilebilir.
    """
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise CryptoError(
            "ENCRYPTION_KEY tanımlı değil — tenant sırları şifrelenemez. "
            "Üretmek için: python -c \"from cryptography.fernet import Fernet;"
            "print(Fernet.generate_key().decode())\""
        )
    from cryptography.fernet import Fernet

    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        raise CryptoError("ENCRYPTION_KEY geçersiz (Fernet anahtarı olmalı).") from e


@lru_cache(maxsize=1)
def _fernet():
    return _load_key()


def reset_key_cache():
    """Test/rotasyon için anahtar önbelleğini sıfırlar."""
    _fernet.cache_clear()


def is_configured():
    """ENCRYPTION_KEY var ve geçerli mi (uygulamayı çökertmeden kontrol)."""
    try:
        _fernet()
        return True
    except CryptoError:
        return False


def encrypt(plaintext):
    """Düz metni şifreler; `enc:v1:<token>` döndürür. Boş/None ise aynen döner."""
    if plaintext is None or plaintext == "":
        return plaintext
    token = _fernet().encrypt(str(plaintext).encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def is_encrypted(value):
    """Değer bu modülün ürettiği şifreli bir token mı?"""
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def decrypt(value):
    """Şifreli token'ı çözer. Şifreli değilse değeri AYNEN döndürür (geçiş uyumu).

    Şifreli görünüp de çözülemeyen bir token için CryptoError yükseltilir
    (fail-closed) — sessizce ham/bozuk değer döndürülmez.
    """
    if value is None or value == "":
        return value
    if not is_encrypted(value):
        # Geçiş dönemi: henüz şifrelenmemiş düz metin değer — olduğu gibi kullan.
        return value
    from cryptography.fernet import InvalidToken

    token = value[len(ENC_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise CryptoError("Sır çözülemedi (anahtar hatalı veya token bozuk).") from e
