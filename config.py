import os
from dotenv import load_dotenv

load_dotenv()

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME") or "gpt-4.1-mini"
AUDIO_MODEL_NAME = "gpt-4o-mini-transcribe"

# ======================================================================
# Instagram (müşteri kanalı) — Meta Instagram Messaging API
# ----------------------------------------------------------------------
# IG_ACCOUNT_ID : Mesaj gönderirken kullanılan Instagram profesyonel hesap
#                 (ya da bağlı Facebook sayfası) kimliği.
# IG_ACCESS_TOKEN : Sayfa/Instagram erişim token'ı (kalıcı System User önerilir).
# IG_GRAPH_VERSION : Graph API sürümü.
# ======================================================================
IG_ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
IG_GRAPH_VERSION = os.getenv("IG_GRAPH_VERSION") or "v23.0"
# API taban adresi — bağlantı yoluna göre değişir:
#   Facebook Sayfası üzerinden  -> graph.facebook.com  (Page access token)
#   Instagram Login ile         -> graph.instagram.com (Instagram user token)
# Hangisi olduğundan emin değilsen: birini dene, panelde "Test Et" başarısızsa
# diğerine çevir. Gönderim ve kurulum testi bu değeri kullanır.
IG_API_BASE = os.getenv("IG_API_BASE") or "graph.facebook.com"

# ======================================================================
# Mağaza bildirimi (satıcı tarafı) — WhatsApp üzerinden.
# Müşteri Instagram'dan gelse de sipariş bildirimi mağazanın WhatsApp
# numarasına gider. Bu alanlar boşsa bildirim atlanır (akış kesilmez).
# ======================================================================
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
STORE_NOTIFY_PHONE = os.getenv("STORE_NOTIFY_PHONE")

# İKAS (ürün ismiyle arama) — WhatsApp projesiyle aynı
IKAS_STORE_NAME = os.getenv("IKAS_STORE_NAME")
IKAS_CLIENT_ID = os.getenv("IKAS_CLIENT_ID")
IKAS_CLIENT_SECRET = os.getenv("IKAS_CLIENT_SECRET")

# Mağaza (Havale/EFT IBAN bilgisi)
STORE_IBAN = os.getenv("STORE_IBAN")
STORE_IBAN_NAME = os.getenv("STORE_IBAN_NAME")

# Dashboard (panel) erişimi — kimlik .env'den okunur, koda gömülmez.
# Parola/hash tanımlı değilse panel erişime kapalıdır (fail-closed).
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
DASHBOARD_PASSWORD_HASH = os.getenv("DASHBOARD_PASSWORD_HASH")

# JWT (panel oturum token'ı) ayarları.
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS") or 12)
COOKIE_SECURE = (os.getenv("COOKIE_SECURE", "true").lower() == "true")

# MySQL — bu proje KENDİ veritabanını kullanır (WhatsApp projesinden ayrı).
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "instaagent")

# Redis — sohbet oturumları. Anahtar prefix'i session_store.py'de ig:session:
REDIS_URL = os.getenv("REDIS_URL")

# App
CACHE_TTL = int(os.getenv("CACHE_TTL") or 600)

# Modele gönderilen ve bellekte saklanan sohbet geçmişi sınırı (mesaj sayısı).
MAX_HISTORY = 12

# Bir oturumda bu kadar mesaj işlendikten sonra geçici durum tazelenir.
LONG_SESSION_MESSAGE_LIMIT = 30
MAX_PRODUCTS = int(os.getenv("MAX_PRODUCTS") or 5)
SESSION_TIMEOUT = 60 * 30
PROCESSED_MESSAGE_TTL = 600

# Webhook doğrulama (Meta paneline birebir aynısı girilir)
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN") or "mumi_verify_token"

# GPT-4.1-mini Pricing (USD / 1M Tokens)
INPUT_TOKEN_PRICE = 0.40
OUTPUT_TOKEN_PRICE = 1.60

CURRENCY_CACHE_TTL = 3600

AVERAGE_CHAT_TIME_MINUTES = 4
EMPLOYEE_HOURLY_COST = 250

# Panel listelerinde sayfa başına kayıt sayısı
PANEL_PAGE_SIZE = int(os.getenv("PANEL_PAGE_SIZE", "50"))


# ======================================================================
# Panelden düzenlenebilen ayarlar — DB (settings tablosu) öncelikli okunur,
# kayıt yoksa .env / kod varsayılanına düşülür.
# ======================================================================

EDITABLE_SETTING_KEYS = (
    "STORE_IBAN",
    "STORE_IBAN_NAME",
    "EMPLOYEE_HOURLY_COST",
    "AVERAGE_CHAT_TIME_MINUTES",
)


def get_setting(key, default=None):
    """settings tablosundaki değeri döndürür; yoksa/erişilemezse default.

    Döngüsel import'u önlemek için settings_service tembel (lazy) yüklenir.
    """
    try:
        from Services.settings_service import get_stored_setting
        val = get_stored_setting(key)
        if val is not None and str(val).strip() != "":
            return val
    except Exception:
        pass
    return default


def _get_float_setting(key, fallback):
    val = get_setting(key)
    if val is None or str(val).strip() == "":
        return fallback
    try:
        return float(val)
    except (TypeError, ValueError):
        return fallback


def store_iban():
    return get_setting("STORE_IBAN", STORE_IBAN)


def store_iban_name():
    return get_setting("STORE_IBAN_NAME", STORE_IBAN_NAME)


def employee_hourly_cost():
    return _get_float_setting("EMPLOYEE_HOURLY_COST", EMPLOYEE_HOURLY_COST)


def average_chat_time_minutes():
    return _get_float_setting("AVERAGE_CHAT_TIME_MINUTES", AVERAGE_CHAT_TIME_MINUTES)
