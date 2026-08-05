import sys

# Windows konsolu (cp1254) emoji içeren print'lerde çökmesin diye UTF-8'e geç
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from fastapi import FastAPI
from fastapi import Request
from fastapi import Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import PlainTextResponse, Response, JSONResponse, RedirectResponse
from fastapi import Form
import json
import time
import re
import csv
import io
import random
from urllib.parse import urlparse, quote
import config
from Services.session_service import (
    store_product,
    build_products_block
)
from Services.session_store import (
    SessionRegistry,
    build_session_store,
    new_session
)
from Services.auth_service import (
    COOKIE_NAME,
    authenticate,
    create_token,
    verify_token,
)
from Services.db import current_tenant_id
from config import (
    JWT_EXPIRE_HOURS,
    COOKIE_SECURE,
    DASHBOARD_USER,
)
from config import (
    MAX_HISTORY,
    LONG_SESSION_MESSAGE_LIMIT,
    VERIFY_TOKEN,
    STORE_NOTIFY_PHONE,
    STORE_IBAN,
    STORE_IBAN_NAME,
    PANEL_PAGE_SIZE,
)
from Services.ikas_service import (
    get_cached_ikas_context,
    get_cached_ikas_context_by_id,
    resolve_product_search,
    match_candidate_by_text,
    _normalize_tr
)
from Services.media_service import (
    download_attachment,
    transcribe_audio
)
# Müşteriye gönderim Instagram üzerinden; mağaza bildirimi WhatsApp üzerinden.
from Services.instagram_service import send_instagram_message
from Services.whatsapp_service import send_whatsapp_message as _send_whatsapp_notify
from Services.conversation_logger import log_message
from Services.openai_service import (
    general_chat,
    product_chat
)
from Services.order_service import format_order_message, save_order, build_order_block, merge_order
from Services.usage_logger import initialize_database
from Services.settings_service import get_all_stored_settings, save_stored_settings
from Services.setup_service import (
    get_setup_state,
    save_section as save_setup_section,
    run_test as run_setup_test,
    mark_complete as mark_setup_complete,
    is_setup_complete,
)
from Services.message_service import is_duplicate
from Services.db import tenant_scope
from Services.tenant_service import (
    extract_ig_account_id,
    resolve_tenant_by_ig_account_id,
)
from Services.meta_verify import verify_webhook_signature, parse_signed_request
from Services import gdpr_service
from Services.models import DEFAULT_TENANT_ID
from Services import onboarding_service
from Services import meta_oauth_service
from Services.dashboard_service import (
    get_dashboard_data,
    get_conversations_list,
    get_conversation_detail,
    get_customers_list,
    get_customer_detail,
    get_ai_usage_detail,
    get_report_summary,
    get_orders_export_rows,
    get_daily_usage_export_rows
)


def send_message(recipient_id, message):
    # Müşteriye Instagram üzerinden mesaj gönderir, ardından giden mesajı
    # conversations tablosuna loglar. (WhatsApp projesindeki send_whatsapp_message
    # sarmalayıcısının Instagram karşılığı.)
    send_instagram_message(recipient_id, message)

    try:
        log_message(recipient_id, "giden", message)
    except Exception as e:
        print("🔴 conversation giden log hatası:", e)


def notify_store(message):
    # Sipariş bildirimi AKTİF TENANT'ın mağaza WhatsApp numarasına gider.
    # Müşteri Instagram'dan gelse de satıcı tarafı WhatsApp'tan bilgilendirilir.
    # Bildirim müşteri sohbeti sayılmaz, conversations'a YAZILMAZ. Gönderim
    # başarısız olsa bile ana akış kesilmez.
    store_notify_phone = config.store_notify_phone()

    if not store_notify_phone:
        print("⚠️ STORE_NOTIFY_PHONE tanımlı değil")
        return

    try:
        _send_whatsapp_notify(store_notify_phone, message)
    except Exception as e:
        print("NOTIFY SEND ERROR:", str(e))


def build_system_prompt():
    """Satış sistem prompt'unu dosyalardan kurar ve güncel ayarları enjekte eder."""
    with open("sales_prompt.txt", "r", encoding="utf-8") as f:
        prompt = f.read()

    prompt = prompt.replace(
        "{IBAN_BILGISI}",
        f"{config.store_iban()} - {config.store_iban_name()}"
    )

    with open("siparis_ozellik_promptu.md", "r", encoding="utf-8") as f:
        prompt = prompt + "\n\n" + f.read()

    return prompt


system_prompt = build_system_prompt()


def reload_system_prompt():
    """Panelden ayar değişince sistem prompt'unu bellekte yeniden kurar."""
    global system_prompt
    system_prompt = build_system_prompt()


general_prompt = open(
    "general_prompt.txt",
    encoding="utf-8"
).read()


def extract_url(text):

    urls = re.findall(
        r"https?://[^\s]+",
        text
    )

    if urls:
        return urls[0]

    return None


def slug_to_query(url):

    # Linkin son yol parçasından (slug) İKAS'ta aranabilir bir ürün adı çıkarır
    path = url.split("?", 1)[0].rstrip("/")

    slug = path.rsplit("/", 1)[-1]

    return slug.replace("-", " ").replace("_", " ").strip()


# Bu alan adlarındaki linklerin slug'ı ürün adı içermez (Instagram post linki vb.);
# bu linkler İKAS'ta ARANMAZ. Mağazanın kendi ürün linkleri slug→İKAS ile çalışır.
SOCIAL_MEDIA_DOMAINS = (
    "instagram.com",
    "facebook.com",
    "fb.me",
    "fb.watch",
    "m.me"
)


def is_social_media_url(url):

    host = urlparse(url).netloc.lower().split(":")[0]

    return any(
        host == domain or host.endswith("." + domain)
        for domain in SOCIAL_MEDIA_DOMAINS
    )


def build_referral_search_text(message_text, referral):

    # Instagram click-to-DM reklamında ürün adı reklamın metnindedir (linkte değil).
    # IG referral yapısı WhatsApp'tan farklıdır: reklam başlığı genelde
    # ads_context_data.ad_title altındadır; ayrıca serbest "ref" dizesi olabilir.
    text_without_urls = re.sub(
        r"https?://[^\s]+",
        " ",
        message_text or ""
    )

    ctx = (referral or {}).get("ads_context_data") or {}

    parts = [
        text_without_urls,
        ctx.get("ad_title") or "",
        (referral or {}).get("ref") or "",
    ]

    return " ".join(p.strip() for p in parts if p and p.strip()).strip()


# Paylaşım açıklamalarında ürün adına özgü OLMAYAN mevsim/pazarlama/CTA kelimeleri.
# Sorgudan atılır ki ayırt edici ürün adı (ör. "Vintage Gömlek") öne çıksın ve
# İKAS araması doğru ürünü döndürsün. Anahtarlar _tr_lower ile normalize edilmiştir
# (ç,ğ,ı,ö,ş,ü -> c,g,i,o,s,u; İ/I sadeleştirme).
_CAPTION_STOPWORDS = {
    "yeni", "sezon",
    # Bu katalogda "viral"/"trend" neredeyse her ürün adında geçen pazarlama
    # kelimeleridir (ayırt edici değil); sorguda kalınca alakasız "viral X"
    # ürünleri eşleşiyor. Ayırt edici kelimelerin öne çıkması için elenirler.
    "viral", "trend",
    "stoklarimizda", "stokta", "stoklarda", "tukeniyor", "tukendi",
    "son", "adet", "kaldi", "sinirli", "sinirli stok",
    "simdi", "hemen", "acele", "siparis", "ver", "verin", "kesfet",
    "tikla", "tiklayin", "link", "linkte", "linkimizde",
    "bio", "biyoda", "biyomuzda", "dm", "mesaj",
    "web", "site", "sitede", "sitemizde", "sitemizden", "www", "com",
    "noureprive", "noure", "prive",
    "kargo", "ucretsiz", "bedava",
    "indirim", "indirimde", "indirimli", "kampanya", "kampanyali",
    "hediyeli", "hediye", "firsat", "fiyat", "geldi", "geliyor",
}


def _product_query_from_caption(title):
    """Paylaşılan gönderi/reel açıklamasından (payload.title) aranabilir ürün adını çıkarır.

    Instagram'da müşteri genelde ürünün postunu/reel'ini DM olarak paylaşır; ne
    ürün adı ne link yazar. Ürün adı açıklamanın İLK satırındadır; sonrasında
    pazarlama metni + site linki gelir. İlk anlamlı satırı alır, URL/emoji'leri
    temizler ve mevsim/pazarlama kelimelerini ayıklayıp ayırt edici ürün adını
    bırakır (ör. "Yeni Sezon Viral Vintage Gömlek Stoklarımızda ✨" -> "Viral Vintage Gömlek").
    """
    if not title:
        return ""

    first_line = ""

    for line in str(title).splitlines():
        if line.strip():
            first_line = line.strip()
            break

    # [metin](url) -> metin ; çıplak URL'leri temizle
    first_line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", first_line)
    first_line = re.sub(r"https?://\S+", " ", first_line)
    first_line = re.sub(r"www\.\S+", " ", first_line)

    # Emoji / sembol / noktalama -> boşluk (Türkçe harfler ve rakamlar korunur)
    first_line = re.sub(r"[^\w\s-]", " ", first_line)

    # Mevsim/pazarlama kelimelerini at; ayırt edici ürün adı kalsın.
    # _normalize_tr İKAS aramasıyla AYNI normalizasyondur (ç,ğ,ı,ö,ş,ü katlanır),
    # böylece stopword'ler (ASCII) Türkçe karakterli kelimelerle de eşleşir.
    kept = [w for w in first_line.split() if _normalize_tr(w) not in _CAPTION_STOPWORDS]

    result = re.sub(r"\s+", " ", " ".join(kept)).strip()

    # Her şey elendiyse (nadiren) temizlenmiş ilk satıra düş — boş sorgu göndermeyelim
    if not result:
        result = re.sub(r"\s+", " ", first_line).strip()

    return result


def looks_like_payment_done(text):

    lower = text.lower()

    # NOT: çıplak "dekont" anahtar DEĞİLDİR — müşteri sadece "dekont" yazınca
    # (ör. "dekontu nereye atayım?") sipariş yanlışlıkla kapanıyordu. Gerçek
    # dekont görsel olarak gelir ve görsel dalında ele alınır. Metinde ise
    # yalnızca AÇIK ödeme-tamamlandı ifadeleri sayılır.
    keywords = [
        "ödedim", "odedim", "ödeme yaptım", "odeme yaptim",
        "havale yaptım", "havale yaptim", "eft yaptım", "eft yaptim",
        "dekont att", "dekont gönder", "dekont gonder", "dekont yolla",
        "dekontu att", "dekontu gönder", "dekontu gonder", "dekontu yolla",
        "parayı yatırdım", "parayi yatirdim",
        "parayı gönderdim", "parayi gonderdim"
    ]

    return any(k in lower for k in keywords)


def close_order_with_receipt(sender):

    # Havale/EFT siparişinde dekont gelince siparişi kapatır.
    notify_store("✅ Ödeme dekontu geldi.")

    chat_sessions[sender]["order_state"] = "tamamlandi"

    send_message(
        sender,
        "Dekontunuz elimize ulaştı, siparişiniz hazırlanıp kargoya "
        "verilecek. Teşekkür ederiz 💕"
    )


def _keep_or_reset_order_state(session):

    # Ödeme bekleyen sipariş (odeme_bekliyor) yeni ürüne geçişte İPTAL EDİLMEZ.
    if session.get("order_state") != "odeme_bekliyor":
        session["order_state"] = None


def _tr_lower(text):
    # Türkçe-duyarlı küçük harf (ör. "BEBE MAVİ" -> "bebe mavi")
    return (text or "").replace("İ", "i").replace("I", "ı").lower()


def _join_tr(items):
    # ["bej", "pudra", "bebe mavi"] -> "bej, pudra ve bebe mavi"
    items = [str(i).strip() for i in items if str(i).strip()]

    if not items:
        return ""

    if len(items) == 1:
        return items[0]

    return ", ".join(items[:-1]) + " ve " + items[-1]


def _price_phrase(context):
    discount = context.get("discount_price")
    price = context.get("price")

    if discount:
        return f"{discount} TL (indirimli)"

    if price:
        return f"{price} TL"

    return None


def _size_phrase(sizes):
    # Sayısalsa "38–50 arası tüm bedenler"; tek/standart bedense "tek beden";
    # değilse bedenleri listeler.
    if not sizes:
        return None

    if len(sizes) == 1:
        only = str(sizes[0]).strip()
        if "beden" in _tr_lower(only):
            return "tek beden"
        return f"{only} bedeni"

    nums = []

    for size in sizes:
        try:
            nums.append(int(str(size).strip()))
        except (TypeError, ValueError):
            nums = None
            break

    if nums and len(nums) >= 3:
        return f"{min(nums)}–{max(nums)} arası tüm bedenler"

    return _join_tr(sizes) + " bedenleri"


# Sabit ama dönüşümlü açılış/kapanışlar — her tanıtımda aynı robotik cümlenin
# tekrarını önler; LLM çağrısı yapılmadığı için token maliyeti sıfırdır.
_INTRO_OPENERS = (
    "Çok şık bir seçim 😊",
    "Harika bir tercih ✨",
    "Bu ürün favorilerimizden 😊",
    "Ah, çok tatlı bir parça 💕",
)

_INTRO_CLOSERS = (
    "Aklınıza takılan bir şey olursa çekinmeden sorabilirsiniz 😊",
    "Merak ettiğiniz bir şey olursa buradayım 💕",
    "Beden ya da renk konusunda yardımcı olmamı isterseniz yazmanız yeterli 😊",
)


def _humanize_product_intro(context, intro=""):
    """Ürünü kuru bir özellik listesi yerine sıcak, butik-çalışanı diliyle tanıtır.

    LLM çağrısı YAPMAZ (ek token maliyeti yok). `intro` verilirse açılış olarak
    o kullanılır (ör. "2 numaralı ürüne geçiyorum"); yoksa sıcak bir açılış seçilir.
    """
    name = (context.get("name") or "").strip()

    colors = context.get("available_colors") or []
    sizes = context.get("available_sizes") or []

    opener = intro.strip() if intro else random.choice(_INTRO_OPENERS)

    parts = [f"{opener} {name}."]

    color_size = []

    if colors:
        color_size.append(
            f"{_join_tr([_tr_lower(c) for c in colors])} renkleriyle"
        )

    size_phrase = _size_phrase(sizes)

    if size_phrase:
        color_size.append(f"{size_phrase} mevcut")

    if color_size:
        # Yalnızca ilk harfi büyüt; beden kısaltmalarını (S, M, XL) bozma.
        sentence = ", ".join(color_size)
        parts.append(sentence[:1].upper() + sentence[1:] + ".")

    price_phrase = _price_phrase(context)

    if price_phrase:
        parts.append(f"Fiyatı {price_phrase}.")

    parts.append(random.choice(_INTRO_CLOSERS))

    return " ".join(parts)


def activate_ikas_product(sender, product_id, intro=""):

    context = get_cached_ikas_context_by_id(product_id)

    if not context:
        return (
            "Ürün bilgisine şu anda ulaşamadım 🙏 Ürün ismini tekrar "
            "yazabilir misiniz?"
        )

    product_key = f"ikas:{product_id}"

    store_product(chat_sessions[sender], product_key, context)

    chat_sessions[sender]["active_url"] = product_key
    _keep_or_reset_order_state(chat_sessions[sender])
    chat_sessions[sender]["pending_products"] = None

    return _humanize_product_intro(context, intro)


def handle_urun_ara(sender, urun_ismi):

    try:
        result = resolve_product_search(urun_ismi)
    except Exception as e:
        print("IKAS SEARCH ERROR:", str(e))
        return (
            "Ürünü ararken kısa süreli bir teknik aksaklık oluştu 🙏 "
            "Ürün ismini tekrar yazabilir ya da ürün linkini gönderebilir misiniz?"
        )

    if result["status"] == "not_found":
        chat_sessions[sender]["pending_products"] = None
        return (
            f"\"{urun_ismi}\" ismiyle bir ürün bulamadım 🙏 Ürün ismini "
            "biraz daha açık yazabilir ya da ürün linkini gönderebilir misiniz?"
        )

    if result["status"] == "multiple":
        chat_sessions[sender]["pending_products"] = result["candidates"]
        chat_sessions[sender]["last_candidates"] = result["candidates"]

        lines = [
            f"{i + 1}) {candidate['name']}"
            for i, candidate in enumerate(result["candidates"])
        ]

        return (
            "Birkaç ürün buldum, hangisini kastediyorsunuz? 😊\n"
            + "\n".join(lines)
        )

    return activate_ikas_product(sender, result["product_id"])


REFERRAL_ASK_PRODUCT_MESSAGE = (
    "Hoş geldiniz 😊 Hangi ürünle ilgilenmiştiniz? "
    "Ürünün ismini yazabilir misiniz?"
)


def handle_referral_search(sender, search_text):

    if not search_text:
        chat_sessions[sender]["pending_products"] = None
        return REFERRAL_ASK_PRODUCT_MESSAGE

    try:
        result = resolve_product_search(search_text)
    except Exception as e:
        print("IKAS REFERRAL SEARCH ERROR:", str(e))
        chat_sessions[sender]["pending_products"] = None
        return REFERRAL_ASK_PRODUCT_MESSAGE

    if result["status"] == "single":
        return activate_ikas_product(sender, result["product_id"])

    if result["status"] == "multiple":
        chat_sessions[sender]["pending_products"] = result["candidates"]
        chat_sessions[sender]["last_candidates"] = result["candidates"]

        lines = [
            f"{i + 1}) {candidate['name']}"
            for i, candidate in enumerate(result["candidates"])
        ]

        return (
            "Hoş geldiniz 😊 Birkaç ürün buldum, hangisini kastediyorsunuz?\n"
            + "\n".join(lines)
        )

    chat_sessions[sender]["pending_products"] = None
    return REFERRAL_ASK_PRODUCT_MESSAGE


def try_resolve_pending_selection(sender, message_text):

    pending = chat_sessions[sender].get("pending_products")

    if not pending:
        return None

    stripped = message_text.strip()

    number_match = re.match(r"^\s*(\d+)", stripped)

    if number_match:
        index = int(number_match.group(1)) - 1

        if 0 <= index < len(pending):
            return activate_ikas_product(sender, pending[index]["id"])

        chat_sessions[sender]["pending_products"] = None
        return None

    matched = match_candidate_by_text(stripped, pending)

    if matched:
        return activate_ikas_product(sender, matched["id"])

    chat_sessions[sender]["pending_products"] = None
    return None


ORDINAL_PREFIXES = (
    ("birinci", 1),
    ("ikinci", 2),
    ("ucuncu", 3),
    ("dorduncu", 4),
    ("besinci", 5)
)

NUMBER_WORDS = {
    "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5
}

CORRECTION_CUES = (
    "yanlis", "pardon", "aslinda", "hayir", "degil",
    "ozur", "kusura", "affedersin", "sehven"
)


def _extract_list_reference(norm_text, candidate_count):

    words = re.findall(r"[a-z0-9]+", norm_text)

    for word in words:

        if word == "ilki" and candidate_count >= 1:
            return 1

        for prefix, index in ORDINAL_PREFIXES:
            if word.startswith(prefix) and index <= candidate_count:
                return index

    match = re.search(r"\b(\d{1,2})\s*(?:numara\w*|nolu|no)\b", norm_text)
    if match:
        index = int(match.group(1))
        return index if 1 <= index <= candidate_count else None

    match = re.search(r"\b(bir|iki|uc|dort|bes)\s*(?:numara\w*|nolu|no)\b", norm_text)
    if match:
        index = NUMBER_WORDS[match.group(1)]
        return index if index <= candidate_count else None

    match = re.search(r"\b(?:numara|no)\s*[:.]?\s*(\d{1,2})\b", norm_text)
    if match:
        index = int(match.group(1))
        return index if 1 <= index <= candidate_count else None

    return None


def try_resolve_candidate_correction(sender, message_text):

    session = chat_sessions[sender]

    candidates = session.get("last_candidates")

    if not candidates:
        return None

    norm = _normalize_tr(message_text)
    words = re.findall(r"[a-z0-9]+", norm)

    has_cue = any(
        word.startswith(cue)
        for word in words
        for cue in CORRECTION_CUES
    )

    if len(words) > 8 and not has_cue:
        return None

    index = _extract_list_reference(norm, len(candidates))

    if index is None and len(candidates) == 2 and re.search(r"\b(digeri|oburu)\b", norm):
        active_url = session.get("active_url") or ""
        if active_url.startswith("ikas:"):
            current_id = active_url.split("ikas:", 1)[1]
            candidate_ids = [c.get("id") for c in candidates]
            if current_id in candidate_ids:
                index = 2 if candidate_ids[0] == current_id else 1

    if index is None:
        return None

    return activate_ikas_product(
        sender,
        candidates[index - 1]["id"],
        intro=f"Tabii, {index} numaralı ürüne geçiyorum 😊"
    )


def refresh_transient_state(session, reset_history=False):

    active_url = session.get("active_url")

    session["products"] = {
        key: context
        for key, context in session["products"].items()
        if key == active_url
    }

    session["pending_products"] = None
    session["last_candidates"] = None

    if session.get("order_state") != "odeme_bekliyor":
        session["order_state"] = None

    if reset_history:
        session["history"] = []


GREETING_WORDS = {
    "merhaba", "merhabalar", "selam", "selamlar", "slm", "mrb",
    "gunaydin", "iyi", "gunler", "aksamlar", "geceler",
    "selamunaleykum", "aleykumselam", "hello", "hi", "hey",
    "hayirli", "isler", "kolay", "gelsin"
}


def is_fresh_greeting(text):

    words = re.findall(r"[a-z]+", _normalize_tr(text))

    return 0 < len(words) <= 4 and all(w in GREETING_WORDS for w in words)


def cleanup_sessions():
    """Süresi dolmuş oturumları temizler (Redis'te TTL yapar; bellek yedeğinde tarar)."""
    expired_count = chat_sessions.cleanup()

    if expired_count:
        print(f"🧹 {expired_count} oturum temizlendi.")


app = FastAPI()
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

templates = Jinja2Templates(directory="templates")
initialize_database()

# Sohbet oturumları dağıtık depoda (Redis) tutulur; uygulama stateless'tır.
chat_sessions = SessionRegistry(build_session_store())


@app.middleware("http")
async def _setup_gate(request: Request, call_next):
    """Kurulum tamamlanmamışsa panel sayfalarını Kurulum ekranına yönlendirir.

    Kurulum durumu AKTİF TENANT'a göredir; bu yüzden JWT'den tenant çözülüp
    o tenant bağlamında kontrol edilir. Oturum yoksa yönlendirme yapılmaz —
    kimlik doğrulama katmanı (require_dashboard_auth) devreye girer (→ /login).
    """
    path = request.url.path

    if path.startswith("/dashboard") and path != "/dashboard/settings/setup":
        ctx = verify_token(request.cookies.get(COOKIE_NAME))
        if ctx is not None:
            scope_token = current_tenant_id.set(ctx["tenant_id"])
            try:
                if not is_setup_complete():
                    return RedirectResponse(
                        url="/dashboard/settings/setup", status_code=307
                    )
            except Exception:
                pass
            finally:
                current_tenant_id.reset(scope_token)

    return await call_next(request)


# ======================================================================
# Panel kimlik doğrulaması — JWT (httpOnly çerez) tabanlı.
# ======================================================================

class AuthRequired(Exception):
    """Geçerli bir oturum çerezi bulunamadığında yükseltilir."""


async def require_dashboard_auth(request: Request):
    """Oturumu doğrular VE isteğin süresi boyunca aktif tenant'ı auth'tan çözer.

    Tenant kimliği yalnızca imzalı JWT'den gelir; böylece panel sorguları
    (scoped session) otomatik olarak doğru tenant'a izole olur. İstek bitince
    tenant bağlamı geri alınır (contextvar sızmaz).

    ASYNC generator dependency: set/reset aynı async context'te olur (sync
    generator'da setup/teardown farklı context'lere düşüp reset'i bozuyordu).
    Değer, sync endpoint'lere threadpool'a context KOPYALANARAK taşınır.
    """
    token = request.cookies.get(COOKIE_NAME)

    ctx = verify_token(token)

    if ctx is None:
        raise AuthRequired()

    scope_token = current_tenant_id.set(ctx["tenant_id"])
    try:
        yield ctx
    finally:
        current_tenant_id.reset(scope_token)


@app.exception_handler(AuthRequired)
async def _auth_required_handler(request: Request, exc: AuthRequired):
    path = request.url.path

    if path.startswith("/admin"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Oturum gerekli."},
        )

    return RedirectResponse(url="/login", status_code=307)


def _set_session_cookie(response, token):
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=JWT_EXPIRE_HOURS * 3600,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if verify_token(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse(url="/dashboard", status_code=307)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    auth_ctx = authenticate(username, password)

    if not auth_ctx:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Kullanıcı adı veya parola hatalı."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = create_token(auth_ctx)

    response = RedirectResponse(url="/dashboard", status_code=303)
    _set_session_cookie(response, token)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=307)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response


@app.get("/", response_class=HTMLResponse)
@app.get("/instagent", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Tanıtım (landing) sayfası. ig.mumifashion.com/instagent altında sunulur."""
    return templates.TemplateResponse(request=request, name="landing.html", context={})


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    """Gizlilik Politikası — PUBLIC (auth'suz). Meta App Review'a bu URL verilir."""
    return templates.TemplateResponse(request=request, name="privacy.html", context={})


@app.get("/terms", response_class=HTMLResponse)
async def terms_of_service(request: Request):
    """Kullanım Koşulları — PUBLIC (auth'suz)."""
    return templates.TemplateResponse(request=request, name="terms.html", context={})


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/kayit")
async def signup_request(request: Request):
    """Landing 'Ücretsiz Dene' talep formu — lead kaydı (public)."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    store_name = (body.get("store_name") or "").strip()
    contact_name = (body.get("contact_name") or "").strip()
    email = (body.get("email") or "").strip().lower()

    if not store_name or not contact_name or "@" not in email:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Mağaza adı, ad-soyad ve geçerli e-posta zorunlu."},
        )

    try:
        from Services.db import get_session
        from Services.models import SignupRequest

        with get_session(scoped=False) as s:
            s.add(SignupRequest(
                store_name=store_name[:255],
                contact_name=contact_name[:255],
                email=email[:255],
                phone=((body.get("phone") or "").strip()[:64]) or None,
                instagram=((body.get("instagram") or "").strip()[:255]) or None,
                message=(body.get("message") or "").strip() or None,
                status="new",
            ))
    except Exception as e:
        print("🔴 signup_request hatası:", e)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": "Kaydedilemedi, lütfen tekrar deneyin."},
        )

    return {"ok": True}


@app.get("/favicon.ico")
def favicon():
    # Tarayıcı varsayılan /favicon.ico isteğini SVG favicon'a yönlendirir
    # (sayfa head'lerinde ayrıca <link rel="icon"> tanımlıdır).
    return RedirectResponse(url="/static/favicon.svg")


@app.get("/product-context")
def product_context(url: str):
    query = slug_to_query(url)
    ai_context, _ = get_cached_ikas_context(query)
    return ai_context or {"error": "not_found", "query": query}


@app.get("/admin/dashboard")
def admin_dashboard(user: str = Depends(require_dashboard_auth)):
    return get_dashboard_data()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="dashboard.html",
                                      context={"is_operator": _is_platform_operator(user)})


# ============ Conversations sayfası ============

@app.get("/dashboard/conversations", response_class=HTMLResponse)
async def conversations_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="conversations.html",
                                      context={"is_operator": _is_platform_operator(user)})


@app.get("/admin/conversations")
def admin_conversations(page: int = 1, user: str = Depends(require_dashboard_auth)):
    return get_conversations_list(page=page, page_size=PANEL_PAGE_SIZE)


@app.get("/admin/conversations/detail")
def admin_conversation_detail(sender: str, page: int = 1, user: str = Depends(require_dashboard_auth)):
    return get_conversation_detail(sender, page=page, page_size=PANEL_PAGE_SIZE)


# ============ Customers sayfası ============

@app.get("/dashboard/customers", response_class=HTMLResponse)
async def customers_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="customers.html",
                                      context={"is_operator": _is_platform_operator(user)})


@app.get("/admin/customers")
def admin_customers(page: int = 1, user: str = Depends(require_dashboard_auth)):
    return get_customers_list(page=page, page_size=PANEL_PAGE_SIZE)


@app.get("/admin/customers/detail")
def admin_customer_detail(phone: str, page: int = 1, user: str = Depends(require_dashboard_auth)):
    return get_customer_detail(phone, page=page, page_size=PANEL_PAGE_SIZE)


# ============ AI Usage sayfası ============

@app.get("/dashboard/ai-usage", response_class=HTMLResponse)
async def ai_usage_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="ai_usage.html",
                                      context={"is_operator": _is_platform_operator(user)})


@app.get("/admin/ai-usage")
def admin_ai_usage(user: str = Depends(require_dashboard_auth)):
    return get_ai_usage_detail()


# ============ Reports sayfası ============

def _csv_response(filename, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)

    content = "﻿" + buf.getvalue()

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/dashboard/reports", response_class=HTMLResponse)
async def reports_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="reports.html",
                                      context={"is_operator": _is_platform_operator(user)})


@app.get("/admin/reports")
def admin_reports(start: str = None, end: str = None, user: str = Depends(require_dashboard_auth)):
    return get_report_summary(start=start, end=end)


@app.get("/admin/reports/export/orders")
def admin_reports_export_orders(start: str = None, end: str = None, user: str = Depends(require_dashboard_auth)):
    rows = get_orders_export_rows(start=start, end=end)

    header = [
        "Tarih", "Musteri No", "Ad Soyad", "Telefon", "Urun", "Renk",
        "Beden", "Adet", "Odeme Sekli", "Teslimat Adresi", "Kayit Tipi"
    ]

    return _csv_response(
        f"siparisler_{start or 'baslangic'}_{end or 'bitis'}.csv",
        header,
        rows
    )


@app.get("/admin/reports/export/usage")
def admin_reports_export_usage(start: str = None, end: str = None, user: str = Depends(require_dashboard_auth)):
    rows = get_daily_usage_export_rows(start=start, end=end)

    header = [
        "Tarih", "Istek", "Prompt Token", "Completion Token",
        "Toplam Token", "Maliyet (USD)"
    ]

    return _csv_response(
        f"ai_kullanim_{start or 'baslangic'}_{end or 'bitis'}.csv",
        header,
        rows
    )


# ============ Settings sayfası ============

_SETTINGS_META = {
    "STORE_IBAN":                {"label": "IBAN", "type": "text"},
    "STORE_IBAN_NAME":           {"label": "IBAN Ad Soyad", "type": "text"},
    "EMPLOYEE_HOURLY_COST":      {"label": "Çalışan Saatlik Ücreti (TL)", "type": "number"},
    "AVERAGE_CHAT_TIME_MINUTES": {"label": "Ortalama Sohbet Süresi (dk)", "type": "number"},
}


def _effective_settings():
    stored = get_all_stored_settings()

    defaults = {
        "STORE_IBAN": config.STORE_IBAN,
        "STORE_IBAN_NAME": config.STORE_IBAN_NAME,
        "EMPLOYEE_HOURLY_COST": config.EMPLOYEE_HOURLY_COST,
        "AVERAGE_CHAT_TIME_MINUTES": config.AVERAGE_CHAT_TIME_MINUTES,
    }

    fields = []
    for key in config.EDITABLE_SETTING_KEYS:
        meta = _SETTINGS_META.get(key, {"label": key, "type": "text"})

        raw = stored.get(key)
        overridden = raw is not None and str(raw).strip() != ""
        value = raw if overridden else defaults.get(key)

        if meta["type"] == "number" and value not in (None, ""):
            try:
                f = float(value)
                value = int(f) if f == int(f) else f
            except (TypeError, ValueError):
                value = defaults.get(key)

        fields.append({
            "key": key,
            "label": meta["label"],
            "type": meta["type"],
            "value": value,
            "default": defaults.get(key),
            "overridden": overridden,
        })

    return {"fields": fields}


@app.get("/dashboard/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="settings.html",
                                      context={"is_operator": _is_platform_operator(user)})


@app.get("/admin/settings")
def admin_settings(user: str = Depends(require_dashboard_auth)):
    return _effective_settings()


@app.post("/admin/settings")
async def admin_settings_save(request: Request, user: str = Depends(require_dashboard_auth)):
    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Geçersiz gövde."})

    to_save = {}

    for key in config.EDITABLE_SETTING_KEYS:

        if key not in body:
            continue

        raw = body[key]
        val = "" if raw is None else str(raw).strip()

        if _SETTINGS_META.get(key, {}).get("type") == "number" and val != "":
            try:
                num = float(val.replace(",", "."))
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": f"{_SETTINGS_META[key]['label']} sayı olmalı."}
                )
            if num < 0:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": f"{_SETTINGS_META[key]['label']} negatif olamaz."}
                )
            val = str(int(num)) if num == int(num) else str(num)

        to_save[key] = val

    if not to_save:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Kaydedilecek alan yok."})

    ok = save_stored_settings(to_save)

    if not ok:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": "Ayarlar kaydedilemedi (DB erişilemiyor olabilir)."}
        )

    reload_system_prompt()

    return {"ok": True, "saved": list(to_save.keys()), "settings": _effective_settings()}


# ======================================================================
# Kurulum (Setup) — SaaS onboarding.
# ======================================================================

@app.get("/dashboard/settings/setup", response_class=HTMLResponse)
async def setup_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="setup.html",
                                      context={"is_operator": _is_platform_operator(user)})


@app.get("/admin/settings/setup")
def admin_setup(user: str = Depends(require_dashboard_auth)):
    return get_setup_state()


@app.post("/admin/settings/setup/save")
async def admin_setup_save(request: Request, user: str = Depends(require_dashboard_auth)):
    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Geçersiz gövde."})

    section = body.get("section")
    fields = body.get("fields") or {}

    res = save_setup_section(section, fields)

    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)

    if section == "company":
        reload_system_prompt()

    res["state"] = get_setup_state()
    return res


@app.post("/admin/settings/setup/test")
async def admin_setup_test(request: Request, user: str = Depends(require_dashboard_auth)):
    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "Geçersiz gövde."})

    return run_setup_test(body.get("section"), body.get("values") or {})


@app.post("/admin/settings/setup/complete")
async def admin_setup_complete(user: str = Depends(require_dashboard_auth)):
    res = mark_setup_complete()

    if not res.get("ok"):
        return JSONResponse(status_code=400, content=res)

    return res


# ======================================================================
# Platform yönetimi (super-admin) + Instagram bağlantısı (OAuth)
# ======================================================================

def require_superadmin(ctx: dict = Depends(require_dashboard_auth)):
    """Yalnız platform operatörü (role=superadmin) erişebilir."""
    if ctx.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Yalnız platform operatörü.")
    return ctx


def _is_platform_operator(ctx):
    """Platform operatörü mü: superadmin YA DA köken (default) tenant sahibi.

    Landing 'Talepler' (lead) verisi platform seviyesidir; yalnız operatör görür.
    Tek-tenant köprüsünde operatör, köken tenant (DEFAULT_TENANT_ID) sahibidir;
    çok-tenant'ta ayrıca superadmin. Normal müşteri tenant'ları (id != default ve
    superadmin değil) bu sekmeyi GÖRMEZ / erişemez.
    """
    if not ctx:
        return False
    return ctx.get("role") == "superadmin" or ctx.get("tenant_id") == DEFAULT_TENANT_ID


def require_platform_operator(ctx: dict = Depends(require_dashboard_auth)):
    """Platform operatörü (superadmin ya da köken tenant sahibi) gerektirir."""
    if not _is_platform_operator(ctx):
        raise HTTPException(status_code=403, detail="Yalnız platform operatörü.")
    return ctx


@app.post("/admin/platform/tenants")
async def admin_create_tenant(request: Request, ctx: dict = Depends(require_superadmin)):
    """Yeni tenant + owner user oluşturur (atomik). Super-admin gerektirir."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    try:
        res = onboarding_service.create_tenant(
            name=body.get("name"),
            owner_email=body.get("owner_email"),
            owner_password=body.get("owner_password"),
            ig_account_id=body.get("ig_account_id"),
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

    return {"ok": True, "tenant": res}


@app.get("/admin/platform/signups")
def admin_list_signups(ctx: dict = Depends(require_platform_operator)):
    """Landing talep formundan gelen lead'leri listeler (platform operatörü)."""
    from Services.db import get_session
    from Services.models import SignupRequest

    with get_session(scoped=False) as s:
        rows = (
            s.query(SignupRequest)
            .order_by(SignupRequest.created_at.desc())
            .limit(200)
            .all()
        )
        items = [{
            "id": r.id, "store_name": r.store_name, "contact_name": r.contact_name,
            "email": r.email, "phone": r.phone, "instagram": r.instagram,
            "message": r.message, "status": r.status,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else None,
        } for r in rows]
    return {"items": items}


@app.get("/dashboard/leads", response_class=HTMLResponse)
async def leads_page(request: Request, user: dict = Depends(require_dashboard_auth)):
    """Landing talep formu lead'leri — YALNIZ platform operatörünün panelinde.

    Operatör değilse (normal müşteri tenant'ı) sessizce dashboard'a döner; sekme
    zaten menüde de gösterilmez."""
    if not _is_platform_operator(user):
        return RedirectResponse(url="/dashboard", status_code=307)
    return templates.TemplateResponse(
        request=request, name="leads.html", context={"is_operator": True}
    )


@app.get("/admin/connect/instagram")
def admin_connect_instagram(ctx: dict = Depends(require_dashboard_auth)):
    """Aktif tenant için Instagram OAuth authorize URL'i üretir (state ile)."""
    url, _ = meta_oauth_service.build_authorize_url(
        ctx["tenant_id"], ctx.get("user_id")
    )
    return {"authorize_url": url}


@app.get("/connect/instagram/callback")
def instagram_oauth_callback(code: str = None, state: str = None,
                             error: str = None, error_description: str = None):
    """OAuth callback — state doğrulanır, token aktif tenant'a şifreli bağlanır.

    Tenant kimliği state'ten çözülür (query'den DEĞİL); token loglanmaz. Tarayıcı
    yönlendirmesi olduğu için Kurulum ekranına geri dönülür (JSON değil): başarıda
    ?connected=1, hatada ?connect_error=... ile — setup.js bu bayrağı gösterir.
    """
    setup_url = "/dashboard/settings/setup"

    def _fail(msg):
        return RedirectResponse(url=f"{setup_url}?connect_error={quote(str(msg))}", status_code=303)

    if error:  # kullanıcı izni reddetti / Meta hata döndürdü
        return _fail(error_description or error)
    if not code or not state:
        return _fail("code ve state gerekli.")
    try:
        meta_oauth_service.handle_callback(state, code)
    except meta_oauth_service.OAuthError as e:
        return _fail(e)

    return RedirectResponse(url=f"{setup_url}?connected=1", status_code=303)


@app.post("/admin/connect/instagram/refresh")
def admin_refresh_instagram(ctx: dict = Depends(require_dashboard_auth)):
    """Aktif tenant'ın uzun ömürlü Instagram token'ını yeniler (~60 gün uzatır)."""
    try:
        res = meta_oauth_service.refresh_token(ctx["tenant_id"])
    except meta_oauth_service.OAuthError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    return res


# ======================================================================
# Meta App Review callback'leri — Data Deletion + Deauthorize
# ----------------------------------------------------------------------
# Her ikisi de gövdede `signed_request` alır (Meta imzalı). İmza
# META_APP_SECRET ile doğrulanır; geçersiz/eksik imzada 403. Sır loglanmaz.
# ======================================================================

async def _extract_signed_request(request: Request):
    """Gövdeden `signed_request` değerini alır (form-encoded ya da JSON)."""
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            body = await request.json()
            return (body or {}).get("signed_request") if isinstance(body, dict) else None
        except Exception:
            return None
    try:
        form = await request.form()
        return form.get("signed_request")
    except Exception:
        return None


@app.post("/data-deletion")
async def data_deletion_callback(request: Request):
    """Meta Veri Silme Talebi callback'i.

    İmzalı `signed_request` içinden `user_id` (IGSID) alınır ve bu kullanıcının
    tüm müşteri verisi silinir. Meta'nın beklediği JSON döner:
    {"url": "<durum takip url'i>", "confirmation_code": "<kod>"}.
    """
    signed_request = await _extract_signed_request(request)
    app_secret = config.META_APP_SECRET

    data = parse_signed_request(signed_request, app_secret) if app_secret else None
    if data is None:
        return JSONResponse(
            status_code=403, content={"error": "invalid signed_request"}
        )

    user_id = data.get("user_id")
    confirmation_code, deleted = gdpr_service.handle_data_deletion(user_id)

    total = sum(deleted.values())
    tail = str(user_id)[-4:] if user_id else "?"
    print(f"🧹 Veri silme talebi işlendi (…{tail}) — {total} kayıt silindi.")

    base = str(request.base_url).rstrip("/")
    return {
        "url": f"{base}/data-deletion/status?code={confirmation_code}",
        "confirmation_code": confirmation_code,
    }


@app.get("/data-deletion/status", response_class=HTMLResponse)
async def data_deletion_status(request: Request, code: str = ""):
    """Veri silme talebi durum sayfası (Meta'ya döndürülen URL buraya işaret eder).

    Silme senkron yapıldığından talep alındıysa veri zaten silinmiştir; sayfa
    kullanıcıya bunu bildirir. Public erişilebilir (auth'suz)."""
    return templates.TemplateResponse(
        request=request,
        name="deletion_status.html",
        context={"code": code},
    )


@app.post("/deauthorize")
async def deauthorize_callback(request: Request):
    """Meta Deauthorize callback'i — kullanıcı uygulamayı kaldırınca çağrılır.

    İmzalı `signed_request` doğrulanır; ilgili tenant'ın Instagram bağlantısı
    pasifleştirilir (status=inactive + token temizlenir). Sır loglanmaz."""
    signed_request = await _extract_signed_request(request)
    app_secret = config.META_APP_SECRET

    data = parse_signed_request(signed_request, app_secret) if app_secret else None
    if data is None:
        return JSONResponse(
            status_code=403, content={"error": "invalid signed_request"}
        )

    user_id = data.get("user_id")
    res = gdpr_service.deauthorize_tenant(user_id)

    tail = str(user_id)[-4:] if user_id else "?"
    if res.get("deactivated"):
        print(f"🔌 Deauthorize (…{tail}) — tenant bağlantısı pasifleştirildi.")
    else:
        print(f"🔌 Deauthorize (…{tail}) — eşleşen tenant yok (fail-safe).")

    return {"ok": True}


# ======================================================================
# Instagram webhook
# ======================================================================

@app.get("/webhook")
async def verify_webhook(request: Request):

    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(content=challenge)

    return PlainTextResponse(content="Verification failed", status_code=403)


@app.post("/webhook")
async def instagram_webhook(request: Request):
    """Instagram webhook girişi — TENANT ROUTING + oturum birim-iş sınırı.

    Akış:
      1) Gövdeyi çöz, olayı alan IG Business Account ID'sini (entry.id) al.
      2) Hesaptan tenant'ı çöz. Eşleşmezse FAIL-CLOSED: işlemeden reddet
         (asla default tenant'a düşme, başka tenant context'inde işleme).
      3) tenant_scope içinde işle → DB/settings/session/AI otomatik izole.

    İstek başında temiz bir oturum kimlik haritası açılır; istek nasıl
    sonlanırsa sonlansın dokunulan oturumlar finally'de kalıcı depoya yazılır.
    """
    # Ham gövde İMZA için gerekli — parse edilmeden önce okunur.
    raw_body = await request.body()

    # X-Hub-Signature-256 doğrulaması (Meta App Review zorunlu). META_APP_SECRET
    # tanımlıysa imza ZORUNLU: eksik/uyuşmayan imzada 403, gövde işlenmez.
    # Karşılaştırma sabit-zamanlı; sır loglanmaz.
    app_secret = config.META_APP_SECRET
    if app_secret:
        signature = request.headers.get("X-Hub-Signature-256")

        if not verify_webhook_signature(raw_body, signature, app_secret):
            print("⛔ Webhook imzası geçersiz/eksik — istek reddedildi (403).")
            return PlainTextResponse(content="invalid signature", status_code=403)
    else:
        print("⚠️ META_APP_SECRET tanımlı değil — webhook imza doğrulaması ATLANDI "
              "(üretimde META_APP_SECRET tanımlanmalıdır).")

    try:
        body = json.loads(raw_body)
    except Exception:
        return {"status": "ignored"}

    if body.get("object") != "instagram":
        # Bu uç yalnız Instagram mesajlaşma olaylarını işler.
        return {"status": "ignored"}

    ig_account_id = extract_ig_account_id(body)
    tenant_id = resolve_tenant_by_ig_account_id(ig_account_id)

    if tenant_id is None:
        # Bilinmeyen/pasif hesap — güvenli log (hesap ID'si maskeli), fail-closed.
        tail = str(ig_account_id)[-4:] if ig_account_id else "?"
        print(f"⛔ Bilinmeyen IG hesabı (…{tail}) — webhook reddedildi (fail-closed).")
        return {"status": "ignored", "reason": "unknown_account"}

    with tenant_scope(tenant_id):
        chat_sessions.begin_request()
        try:
            return await _process_instagram_webhook(body)
        finally:
            chat_sessions.flush()


def _parse_instagram_event(body):
    """IG webhook gövdesinden ilk mesaj olayını normalize eder.

    Döner: (sender, message_id, event) ya da None (işlenecek mesaj yoksa).
    IG payload'u Messenger tarzıdır: entry[].messaging[] → sender.id (IGSID),
    message.mid/text/attachments/referral, ya da postback (ice breaker).
    """
    entry = (body.get("entry") or [{}])[0]
    events = entry.get("messaging") or entry.get("standby") or []

    if not events:
        return None

    event = events[0]

    sender = (event.get("sender") or {}).get("id")

    if not sender:
        return None

    return sender, event


async def _process_instagram_webhook(body):
    """Tenant scope'u ÇAĞIRAN tarafından ayarlanmış (tenant_scope) parsed gövdeyi işler."""
    cleanup_sessions()

    print("INSTAGRAM WEBHOOK:")
    print(json.dumps(body, indent=2, ensure_ascii=False))

    parsed = _parse_instagram_event(body)

    if parsed is None:
        return {"status": "ok"}

    sender, event = parsed

    try:

        message = event.get("message")

        # Kendi gönderdiğimiz mesajın echo'su → yoksay (döngüyü önler)
        if message and message.get("is_echo"):
            return {"status": "ok"}

        # ---- İçerik + mesaj kimliği çıkarımı ----
        message_text = None
        referral = None
        message_id = None

        if message:

            message_id = message.get("mid")
            referral = message.get("referral") or event.get("referral")

            if message.get("text"):

                message_text = message["text"]

            elif message.get("attachments"):

                attachment = message["attachments"][0] or {}
                atype = (attachment.get("type") or "").lower()
                payload = attachment.get("payload") or {}
                media_url = payload.get("url")
                # ig_post / ig_reel paylaşımlarında ürün açıklaması burada gelir
                shared_title = payload.get("title")

                if atype == "audio" and media_url:

                    if message_id and is_duplicate(message_id):
                        print(f"⚠️ Duplicate Message: {message_id}")
                        return {"status": "duplicate"}

                    audio_bytes = download_attachment(media_url)
                    message_text = transcribe_audio(audio_bytes)

                elif atype == "image":

                    if message_id and is_duplicate(message_id):
                        return {"status": "duplicate"}

                    log_message(sender, "gelen", "[görsel]")

                    session = chat_sessions.get(sender)

                    if session and session.get("order_state") == "odeme_bekliyor":
                        close_order_with_receipt(sender)
                        return {"status": "ok"}

                    send_message(
                        sender,
                        "Şu an yazılı ve sesli mesajları yanıtlayabiliyorum 😊"
                    )
                    return {"status": "ok"}

                elif shared_title:

                    # Paylaşılan ürün gönderisi / reel'i (ig_post, ig_reel, share...):
                    # açıklama (payload.title) ürün adını içerir. Instagram'da müşteri
                    # çoğunlukla ürünün postunu paylaşır — ad/link yazmaz. Açıklamadan
                    # ürün adını çıkarıp katalogda arıyoruz (Instagram'ın ana giriş yolu).
                    if message_id and is_duplicate(message_id):
                        return {"status": "duplicate"}

                    query = _product_query_from_caption(shared_title)

                    if sender not in chat_sessions:
                        chat_sessions[sender] = new_session()
                    chat_sessions[sender]["last_activity"] = time.time()

                    log_message(
                        sender,
                        "gelen",
                        f"[paylaşılan ürün] {query}" if query else "[paylaşılan gönderi]"
                    )

                    if not query:
                        send_message(
                            sender,
                            "Paylaştığınız ürünü tam seçemedim 🙏 Ürünün ismini "
                            "yazabilir misiniz? 😊"
                        )
                        return {"status": "ok"}

                    # Paylaşılan ürünü SESSİZCE aktive et — paylaşımın kendisine ayrı
                    # mesaj atma. Müşteri postu paylaşıp ardından "ne kadar?" gibi bir
                    # soru yazdığında, o soruya TEK ve net cevap verilsin (paylaşıma
                    # "buldum" + soruya "fiyat" şeklinde çift mesaj oluşmasın).
                    # Yalnız birden çok aday ya da bulunamama durumunda yönlendirme gerekir.
                    try:
                        result = resolve_product_search(query)
                    except Exception as e:
                        print("IKAS SHARED SEARCH ERROR:", str(e))
                        send_message(
                            sender,
                            "Paylaştığınız ürünü ararken kısa bir aksaklık oldu 🙏 "
                            "Ürün ismini yazabilir misiniz?"
                        )
                        return {"status": "ok"}

                    if result["status"] == "not_found":
                        chat_sessions[sender]["pending_products"] = None
                        send_message(
                            sender,
                            "Paylaştığınız ürünü tam seçemedim 🙏 Ürünün ismini "
                            "yazabilir misiniz? 😊"
                        )
                        return {"status": "ok"}

                    if result["status"] == "multiple":
                        chat_sessions[sender]["pending_products"] = result["candidates"]
                        chat_sessions[sender]["last_candidates"] = result["candidates"]
                        lines = [
                            f"{i + 1}) {c['name']}"
                            for i, c in enumerate(result["candidates"])
                        ]
                        send_message(
                            sender,
                            "Paylaştığınız ürüne yakın birkaç ürün buldum, hangisi? 😊\n"
                            + "\n".join(lines)
                        )
                        return {"status": "ok"}

                    # Tek eşleşme: sessizce aktive et, mesaj GÖNDERME.
                    activate_ikas_product(sender, result["product_id"])
                    return {"status": "ok"}

                else:

                    # video / story_mention / reaction / desteklenmeyen (title yok) tip.
                    # Bunlar genelde gerçek bir müşteri sorusu değildir; otomatik yanıt
                    # "kendi kendine mesaj" gürültüsüne yol açıyordu — sessizce yok say.
                    print(f"ℹ️ İşlenmeyen ek tipi, yanıt verilmedi: {atype}")
                    return {"status": "ok"}

            elif referral:
                # Reklamdan gelen ilk mesaj metinsiz olabilir; referral akışına düşer
                message_text = ""

            else:
                return {"status": "ok"}

        elif event.get("postback"):

            postback = event["postback"]
            referral = postback.get("referral")
            message_id = postback.get("mid") or f"pb:{sender}:{event.get('timestamp')}"
            message_text = postback.get("title") or postback.get("payload") or ""

        else:
            # read / delivery / reaction gibi olaylar → işlenmez
            return {"status": "ok"}

        # ---- Duplicate guard (audio/image kendi içinde ele alındı) ----
        if message_id and message and message.get("text") and is_duplicate(message_id):
            print(f"⚠️ Duplicate Message: {message_id}")
            return {"status": "duplicate"}

        print("SENDER:", sender)
        print("MESSAGE:", message_text)

        # Gelen müşteri mesajı (metin/transkript) konuşma geçmişine loglanır.
        if message_text:
            log_message(sender, "gelen", message_text)

        if sender not in chat_sessions:
            chat_sessions[sender] = new_session()

        chat_sessions[sender]["last_activity"] = time.time()

        session = chat_sessions[sender]

        session["message_count"] = session.get("message_count", 0) + 1

        if (
            session["message_count"] >= LONG_SESSION_MESSAGE_LIMIT
            and not session.get("pending_products")
        ):
            print("🧽 Uzun oturum: geçici durum tazelendi")
            refresh_transient_state(session)
            session["message_count"] = 0

        if is_fresh_greeting(message_text):
            refresh_transient_state(session, reset_history=True)

        url = extract_url(message_text)

        if referral:
            print(
                "📣 IG REKLAM/REFERRAL — "
                f"source: {referral.get('source')}, "
                f"type: {referral.get('type')}, "
                f"ref: {referral.get('ref')}"
            )

        social_url = url is not None and is_social_media_url(url)

        # Bekleyen ürün adayı listesi varsa mesaj önce seçim olarak yorumlanır
        if not url and not referral:

            pending_answer = try_resolve_pending_selection(sender, message_text)
            if pending_answer is not None:
                send_message(sender, pending_answer)
                return {"status": "ok"}

            correction_answer = try_resolve_candidate_correction(sender, message_text)
            if correction_answer is not None:
                send_message(sender, correction_answer)
                return {"status": "ok"}

        # Reklam metninden ürün bulma / sosyal medya linki
        if social_url or (referral and not url):

            chat_sessions[sender]["pending_products"] = None

            if referral:
                assistant_answer = handle_referral_search(
                    sender,
                    build_referral_search_text(message_text, referral)
                )
            else:
                assistant_answer = (
                    "Bu linkteki ürünü göremiyorum 🙏 Hangi ürünle "
                    "ilgilenmiştiniz? Ürünün ismini yazabilir misiniz?"
                )

            send_message(sender, assistant_answer)
            return {"status": "ok"}

        if url:

            chat_sessions[sender]["pending_products"] = None

            search_query = slug_to_query(url)

            ai_context, product_id = get_cached_ikas_context(search_query)

            if not ai_context:
                send_message(
                    sender,
                    "Bu linkteki ürünü bulamadım 🙏 Ürünün ismini yazabilir misiniz?"
                )
                return {"status": "ok"}

            product_key = f"ikas:{product_id}"

            store_product(chat_sessions[sender], product_key, ai_context)

            chat_sessions[sender]["active_url"] = product_key
            _keep_or_reset_order_state(chat_sessions[sender])

            print("KAYDEDİLEN ÜRÜN:", chat_sessions[sender]["active_url"])

            cleaned_message = message_text.replace(url, "").strip()

            if not cleaned_message:
                send_message(sender, _humanize_product_intro(ai_context))
                return {"status": "ok"}

            message_text = cleaned_message

        active_url = chat_sessions[sender]["active_url"]

        order_state = chat_sessions[sender].get("order_state")

        if order_state == "odeme_bekliyor" and looks_like_payment_done(message_text):
            close_order_with_receipt(sender)
            return {"status": "ok"}

        lower_message = message_text.lower()

        if any(
                phrase in lower_message
                for phrase in [
                    "başka ürün", "farklı ürün", "ürün linki göndereyim",
                    "link göndereyim", "başka bir ürün", "başka ürün hakkında"
                ]
        ):
            send_message(
                sender,
                "Tabii 😊 İncelememi istediğiniz ürünün linkini gönderebilirsiniz."
            )
            return {"status": "ok"}

        if not active_url:
            response = general_chat(general_prompt, message_text, sender)

            tool_call = response.get("tool_call")

            if tool_call and tool_call["name"] == "urun_ara":
                assistant_answer = handle_urun_ara(
                    sender,
                    tool_call["arguments"].get("urun_ismi", message_text)
                )
            else:
                assistant_answer = response["answer"]
                if not assistant_answer:
                    assistant_answer = "Bu konuda size nasıl yardımcı olabilirim? 😊"

            send_message(sender, assistant_answer)
            return {"status": "ok"}

        try:

            if active_url and active_url.startswith("ikas:"):

                product_id = active_url.split("ikas:", 1)[1]

                fresh_context = get_cached_ikas_context_by_id(product_id)

                if fresh_context:
                    store_product(chat_sessions[sender], active_url, fresh_context)

            products_block = build_products_block(chat_sessions[sender])

            history = chat_sessions[sender]["history"][-MAX_HISTORY:]

            order_block = ""

            if order_state is not None:
                order_block = build_order_block(chat_sessions[sender].get("last_order"))

            response = product_chat(
                # Sistem promptu HER İSTEKTE aktif tenant'a göre kurulur
                # (mağazanın IBAN'ı vb. tenant ayarlarından enjekte edilir).
                build_system_prompt(),
                products_block,
                history,
                message_text,
                sender,
                include_order_tool=(order_state is None),
                include_update_tool=(order_state is not None),
                order_block=order_block
            )
            print(response)  # geçici

            tool_call = response.get("tool_call")

            if tool_call and tool_call["name"] == "siparis_olustur":

                order = tool_call["arguments"]

                notify_store(format_order_message(order))

                save_order(sender, order, is_update=False)

                chat_sessions[sender]["last_order"] = order

                if order.get("odeme_sekli") == "Havale/EFT":

                    chat_sessions[sender]["order_state"] = "odeme_bekliyor"

                    assistant_answer = (
                        "Siparişiniz alındı 😊 Ödemenizi yaptıktan sonra "
                        "siparişiniz hazırlanıp kargoya verilecektir. "
                        "Dekontunuzu buraya iletebilirsiniz 💕"
                    )

                else:

                    chat_sessions[sender]["order_state"] = "tamamlandi"

                    assistant_answer = (
                        "Siparişiniz alındı 😊 En kısa sürede hazırlanıp "
                        "kargoya verilecek. Kargo takip numaranız mesaj olarak "
                        "tarafınıza iletilecek 💕"
                    )

            elif tool_call and tool_call["name"] == "siparis_guncelle":

                order = merge_order(
                    chat_sessions[sender].get("last_order"),
                    tool_call["arguments"]
                )

                chat_sessions[sender]["last_order"] = order

                notify_store(format_order_message(order, is_update=True))

                save_order(sender, order, is_update=True)

                assistant_answer = (
                    "Siparişinizdeki değişikliği aldım ve güncelledim 😊 "
                    "Yeni bilgileriniz ekibimize iletildi. Başka bir değişiklik "
                    "olursa çekinmeden yazabilirsiniz 💕"
                )

            elif tool_call and tool_call["name"] == "urun_ara":

                assistant_answer = handle_urun_ara(
                    sender,
                    tool_call["arguments"].get("urun_ismi", message_text)
                )

            else:

                assistant_answer = response["answer"]

                if not assistant_answer:
                    assistant_answer = "Bu konuda size nasıl yardımcı olabilirim? 😊"

            chat_sessions[sender]["history"].append(
                {"role": "user", "content": message_text}
            )

            chat_sessions[sender]["history"].append(
                {"role": "assistant", "content": assistant_answer}
            )

            chat_sessions[sender]["history"] = (
                chat_sessions[sender]["history"][-MAX_HISTORY:]
            )

            send_message(sender, assistant_answer)

        except Exception as e:

            print("PRODUCT ERROR:", str(e))

            send_message(sender, "Ürün bilgisi alınırken hata oluştu.")

    except Exception as e:

        print("WEBHOOK ERROR:")
        print(str(e))

        try:
            send_message(
                sender,
                "Şu anda kısa süreli teknik bir aksaklık oluştu 🙏 Lütfen birkaç dakika sonra tekrar dener misiniz?"
            )
        except Exception:
            pass

        return {"status": "error"}

    return {"status": "ok"}
