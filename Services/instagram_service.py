"""Instagram Messaging API — müşteriye mesaj gönderimi.

WhatsApp projesindeki whatsapp_service.py'nin ikizidir; tek fark endpoint ve
gövde biçimidir. Instagram, Messenger Platform tarzı bir gönderim kullanır:

    POST https://graph.facebook.com/<ver>/<IG_ACCOUNT_ID>/messages
    body: {"recipient": {"id": <IGSID>}, "message": {"text": <metin>}}
    header: Authorization: Bearer <IG_ACCESS_TOKEN>

24 SAAT PENCERESİ: Instagram'da işletme, kullanıcının son mesajından itibaren
24 saat içinde serbest metin gönderebilir. Bot her zaman gelen mesaja anında
yanıt verdiği için normal akışta pencere içindedir; 24 saatten sonra proaktif
mesaj göndermek API tarafından reddedilir (bu bilinçli bir platform kısıtıdır).
"""

import requests

from config import (
    IG_ACCOUNT_ID,
    IG_ACCESS_TOKEN,
    IG_GRAPH_VERSION,
    IG_API_BASE,
)


def send_instagram_message(recipient_id, message):
    """Instagram kullanıcısına (IGSID) metin mesajı gönderir.

    Taban adres IG_API_BASE'ten gelir (Facebook Sayfası yolu için
    graph.facebook.com, Instagram Login yolu için graph.instagram.com).
    """

    url = (
        f"https://{IG_API_BASE}/{IG_GRAPH_VERSION}/"
        f"{IG_ACCOUNT_ID}/messages"
    )

    headers = {
        "Authorization": f"Bearer {IG_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message},
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30,
    )

    print("IG STATUS:", response.status_code)
    print("IG RESPONSE:", response.text)
