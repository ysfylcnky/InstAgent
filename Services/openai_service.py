from openai import OpenAI
import time
import json
import config
from Services.usage_logger import log_usage
from Services.order_service import SIPARIS_TOOL, SIPARIS_GUNCELLE_TOOL
from Services.ikas_service import URUN_ARA_TOOL
from config import (
    INPUT_TOKEN_PRICE,
    OUTPUT_TOKEN_PRICE,
    CACHED_INPUT_DISCOUNT,
    MAX_OUTPUT_TOKENS
)

# OpenAI client'ı AKTİF TENANT'ın anahtarıyla, tenant başına (lazy) kurulur.
# Böylece her tenant kendi OpenAI hesabını/faturasını kullanır.
_clients = {}


def _get_client():
    from Services.db import get_current_tenant
    from Services.models import DEFAULT_TENANT_ID

    tenant = get_current_tenant()
    if tenant is None:
        tenant = DEFAULT_TENANT_ID

    client = _clients.get(tenant)
    if client is None:
        client = OpenAI(api_key=config.openai_api_key())
        _clients[tenant] = client
    return client


def invalidate_client(tenant_id=None):
    """OpenAI anahtarı değişince tenant client cache'ini temizler."""
    if tenant_id is None:
        _clients.clear()
    else:
        _clients.pop(tenant_id, None)


def _create_chat(messages, sender, tools=None):

    start_time = time.time()

    client = _get_client()
    model_name = config.model_name()

    # tools verilmişse modele tool calling imkanı tanınır.
    # max_tokens: çıktı token tavanı (maliyet kontrolü).
    if tools:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=MAX_OUTPUT_TOKENS
        )
    else:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS
        )

    response_time = time.time() - start_time

    usage = response.usage

    # Prompt caching: tekrar eden ön-ek (sabit sistem promptu) %50 indirimli
    # faturalanır. cached_tokens'ı ayırıp gerçek (indirimli) maliyeti hesapla;
    # aksi halde panel gerçek OpenAI faturasından yüksek görünür.
    cached_tokens = 0
    try:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached_tokens = getattr(details, "cached_tokens", 0) or 0
    except Exception:
        cached_tokens = 0

    uncached_prompt_tokens = max(usage.prompt_tokens - cached_tokens, 0)

    prompt_cost = (
        uncached_prompt_tokens / 1_000_000 * INPUT_TOKEN_PRICE
        + cached_tokens / 1_000_000 * INPUT_TOKEN_PRICE * CACHED_INPUT_DISCOUNT
    )

    completion_cost = (
                              usage.completion_tokens
                              / 1_000_000
                      ) * OUTPUT_TOKEN_PRICE

    total_cost = prompt_cost + completion_cost

    log_usage(

        sender=sender,

        model=model_name,

        prompt_tokens=response.usage.prompt_tokens,

        completion_tokens=response.usage.completion_tokens,

        total_tokens=response.usage.total_tokens,

        cost=round(total_cost, 6),

        response_time=round(response_time, 3)

    )

    message = response.choices[0].message

    # Modelin döndürdüğü tool çağrısı varsa ilkini parse et
    tool_call = None

    if message.tool_calls:

        first_call = message.tool_calls[0]

        tool_call = {
            "name": first_call.function.name,
            "arguments": json.loads(first_call.function.arguments)
        }

    return {

        "answer": message.content,

        "tool_call": tool_call,

        "prompt_tokens": response.usage.prompt_tokens,

        "completion_tokens": response.usage.completion_tokens,

        "total_tokens": response.usage.total_tokens,

        "response_time": round(response_time, 3),

        "cost": round(total_cost, 6)

    }

def general_chat(
    general_prompt,
    message_text,
    sender
):

    messages = [

        {
            "role": "system",
            "content": general_prompt
        },

        {
            "role": "user",
            "content": message_text
        }

    ]

    # urun_ara tool'u ile müşteri ürünü isimle de sorabilir
    return _create_chat(
        messages,
        sender,
        tools=[URUN_ARA_TOOL]
    )

def product_chat(
    system_prompt,
    products_block,
    history,
    message_text,
    sender,
    include_order_tool=True,
    include_update_tool=False,
    order_block=""
):

    system_content = (
        system_prompt
        + "\n\nÜrün Bilgileri:\n"
        + products_block
    )

    # Oluşturulmuş bir sipariş varsa (güncelleme akışı) mevcut sipariş modele
    # bağlam olarak verilir; böylece değişmeyen alanlar baştan sorulmaz/null olmaz.
    if order_block:
        system_content = (
            system_content
            + "\n\nMEVCUT SİPARİŞ (yalnızca güncelleme içindir):\n"
            + order_block
        )

    messages = [

        {
            "role": "system",
            "content": system_content
        },

        *history,

        {
            "role": "user",
            "content": message_text
        }

    ]

    # urun_ara her zaman verilir (isimle ürün sorgusu link akışına ek).
    # siparis_olustur tool'u yalnızca yeni sipariş alınabilir durumda (order_state None) verilir.
    # Sipariş zaten oluşturulmuşsa onun yerine siparis_guncelle verilir; böylece müşteri
    # sonradan sipariş değişikliği (adres/ürün/renk/beden/adet/ödeme) isteyebilir.
    tools = [URUN_ARA_TOOL]

    if include_order_tool:
        tools.append(SIPARIS_TOOL)

    if include_update_tool:
        tools.append(SIPARIS_GUNCELLE_TOOL)

    return _create_chat(
        messages,
        sender,
        tools=tools
    )