"""Meta imza doğrulama — webhook (X-Hub-Signature-256) + signed_request.

Meta App Review'ın zorunlu kıldığı üç uç (webhook POST, Data Deletion,
Deauthorize) gelen gövdenin gerçekten Meta'dan geldiğini kanıtlamak için
platform sırrı `META_APP_SECRET` ile HMAC-SHA256 imza kullanır.

İki farklı format vardır:
  * Webhook POST → `X-Hub-Signature-256: sha256=<hex>` başlığı, HAM gövde
    (raw bytes) üzerinden hesaplanır. (`verify_webhook_signature`)
  * Data Deletion / Deauthorize → gövdede `signed_request` alanı:
    `base64url(sig).base64url(json_payload)`; imza base64url payload DİZESİ
    üzerinden hesaplanır. (`parse_signed_request`)

Kurallar:
  * Karşılaştırma SABİT ZAMANLI (`hmac.compare_digest`) — timing sızıntısı yok.
  * `app_secret` yoksa fonksiyonlar False/None döner; "secret tanımlı değilse
    ne yapılacağı" kararı çağırana (main.py) bırakılır.
  * Sır asla loglanmaz; bu modül hiçbir şey yazdırmaz.
"""

import base64
import hashlib
import hmac
import json


def _b64url_decode(value):
    """Padding'i eksik olabilen base64url dizesini güvenle çözer (bytes döner)."""
    raw = value.encode("ascii") if isinstance(value, str) else value
    padding = b"=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def verify_webhook_signature(raw_body, signature_header, app_secret):
    """`X-Hub-Signature-256` başlığını ham gövde üzerinden HMAC-SHA256 doğrular.

    raw_body: request'in HAM bytes gövdesi (parse edilmiş JSON DEĞİL).
    signature_header: "sha256=<hex>" biçimi (yoksa None).
    app_secret: META_APP_SECRET (yoksa doğrulama başarısız sayılır).

    Uyuşuyorsa True; imza yok/biçimsiz/uyuşmuyorsa False.
    """
    if not app_secret or not signature_header:
        return False

    if isinstance(raw_body, str):
        raw_body = raw_body.encode("utf-8")

    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    # Başlık "sha256=<hex>" gelir; öneki ayıklayıp yalnız hex'i karşılaştırırız.
    prefix, _, provided = signature_header.partition("=")
    if prefix.strip().lower() != "sha256" or not provided:
        return False

    return hmac.compare_digest(expected, provided.strip())


def parse_signed_request(signed_request, app_secret):
    """Meta `signed_request`'i doğrular ve çözülmüş JSON payload'ı (dict) döner.

    Biçim: `base64url(sig).base64url(payload_json)`. İmza, base64url payload
    DİZESİ üzerinden HMAC-SHA256 ile hesaplanır (çözülmüş JSON değil).

    Geçerliyse payload dict; imza/biçim/algoritma hatalıysa None (fail-closed).
    """
    if not signed_request or not app_secret:
        return None

    try:
        encoded_sig, encoded_payload = signed_request.split(".", 1)
    except ValueError:
        return None

    try:
        provided_sig = _b64url_decode(encoded_sig)
        data = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    algorithm = str(data.get("algorithm", "HMAC-SHA256")).upper()
    if algorithm not in ("HMAC-SHA256", "HMAC_SHA256"):
        return None

    expected_sig = hmac.new(
        app_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()

    if not hmac.compare_digest(expected_sig, provided_sig):
        return None

    return data


def build_signed_request(payload, app_secret):
    """TEST yardımcı: verilen payload'dan geçerli bir `signed_request` üretir.

    Üretim kodu bunu kullanmaz; yalnız testlerin geçerli imzalı gövde
    oluşturması içindir (Meta'nın imzalama biçiminin aynısı).
    """
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).rstrip(b"=")
    sig = hmac.new(
        app_secret.encode("utf-8"), encoded_payload, hashlib.sha256
    ).digest()
    encoded_sig = base64.urlsafe_b64encode(sig).rstrip(b"=")
    return f"{encoded_sig.decode('ascii')}.{encoded_payload.decode('ascii')}"
