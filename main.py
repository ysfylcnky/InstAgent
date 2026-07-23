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
from urllib.parse import urlparse
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
    verify_credentials,
    create_token,
    verify_token,
)
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
    # Sipariş bildirimi mağazanın WhatsApp numarasına (STORE_NOTIFY_PHONE) gider.
    # Müşteri Instagram'dan gelse de satıcı tarafı WhatsApp'tan bilgilendirilir.
    # Bildirim müşteri sohbeti sayılmaz, conversations'a YAZILMAZ. Gönderim
    # başarısız olsa bile ana akış kesilmez.
    if not STORE_NOTIFY_PHONE:
        print("⚠️ STORE_NOTIFY_PHONE tanımlı değil")
        return

    try:
        _send_whatsapp_notify(STORE_NOTIFY_PHONE, message)
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


def looks_like_payment_done(text):

    lower = text.lower()

    keywords = [
        "ödedim", "odedim", "ödeme yaptım", "odeme yaptim",
        "havale yaptım", "havale yaptim", "eft yaptım", "eft yaptim",
        "dekont", "parayı yatırdım", "parayi yatirdim",
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

    detail = ""

    if context.get("available_colors"):
        detail += " Renkler: " + ", ".join(context["available_colors"]) + "."

    if context.get("available_sizes"):
        detail += " Bedenler: " + ", ".join(context["available_sizes"]) + "."

    if context.get("discount_price"):
        detail += f" Fiyatı {context['discount_price']} TL (indirimli)."
    elif context.get("price"):
        detail += f" Fiyatı {context['price']} TL."

    prefix = f"{intro} " if intro else ""

    return (
        f"{prefix}{context.get('name', '')}.{detail} "
        "Bu ürünle ilgili sorularınızı sorabilirsiniz."
    )


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
    """Kurulum tamamlanmamışsa panel sayfalarını Kurulum ekranına yönlendirir."""
    path = request.url.path

    if path.startswith("/dashboard") and path != "/dashboard/settings/setup":
        try:
            if not is_setup_complete():
                return RedirectResponse(url="/dashboard/settings/setup", status_code=307)
        except Exception:
            pass

    return await call_next(request)


# ======================================================================
# Panel kimlik doğrulaması — JWT (httpOnly çerez) tabanlı.
# ======================================================================

class AuthRequired(Exception):
    """Geçerli bir oturum çerezi bulunamadığında yükseltilir."""


def require_dashboard_auth(request: Request):
    token = request.cookies.get(COOKIE_NAME)

    username = verify_token(token)

    if username is None:
        raise AuthRequired()

    return username


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
    if not verify_credentials(username, password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Kullanıcı adı veya parola hatalı."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = create_token(username)

    response = RedirectResponse(url="/dashboard", status_code=303)
    _set_session_cookie(response, token)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=307)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response


@app.get("/")
def home():
    return {"status": "ok"}


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
    return templates.TemplateResponse(request=request, name="dashboard.html", context={})


# ============ Conversations sayfası ============

@app.get("/dashboard/conversations", response_class=HTMLResponse)
async def conversations_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="conversations.html", context={})


@app.get("/admin/conversations")
def admin_conversations(page: int = 1, user: str = Depends(require_dashboard_auth)):
    return get_conversations_list(page=page, page_size=PANEL_PAGE_SIZE)


@app.get("/admin/conversations/detail")
def admin_conversation_detail(sender: str, page: int = 1, user: str = Depends(require_dashboard_auth)):
    return get_conversation_detail(sender, page=page, page_size=PANEL_PAGE_SIZE)


# ============ Customers sayfası ============

@app.get("/dashboard/customers", response_class=HTMLResponse)
async def customers_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="customers.html", context={})


@app.get("/admin/customers")
def admin_customers(page: int = 1, user: str = Depends(require_dashboard_auth)):
    return get_customers_list(page=page, page_size=PANEL_PAGE_SIZE)


@app.get("/admin/customers/detail")
def admin_customer_detail(phone: str, page: int = 1, user: str = Depends(require_dashboard_auth)):
    return get_customer_detail(phone, page=page, page_size=PANEL_PAGE_SIZE)


# ============ AI Usage sayfası ============

@app.get("/dashboard/ai-usage", response_class=HTMLResponse)
async def ai_usage_page(request: Request, user: str = Depends(require_dashboard_auth)):
    return templates.TemplateResponse(request=request, name="ai_usage.html", context={})


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
    return templates.TemplateResponse(request=request, name="reports.html", context={})


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
    return templates.TemplateResponse(request=request, name="settings.html", context={})


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
    return templates.TemplateResponse(request=request, name="setup.html", context={})


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
    """Oturum birim-iş (unit of work) sınırı.

    İstek başında temiz bir kimlik haritası açılır; istek nasıl sonlanırsa
    sonlansın dokunulan oturumlar finally içinde tek seferde kalıcı depoya yazılır.
    """
    chat_sessions.begin_request()

    try:
        return await _process_instagram_webhook(request)
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


async def _process_instagram_webhook(request: Request):
    cleanup_sessions()
    body = await request.json()

    if body.get("object") != "instagram":
        # Bu uç yalnız Instagram mesajlaşma olaylarını işler.
        return {"status": "ignored"}

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

                else:

                    # video / share / story_mention vb.
                    send_message(
                        sender,
                        "Şu an yazılı ve sesli mesajları yanıtlayabiliyorum 😊"
                    )
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
                send_message(
                    sender,
                    "Ürünü görüntüledim 😊 Bu ürünle ilgili sorularınızı sorabilirsiniz."
                )
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
                system_prompt,
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
