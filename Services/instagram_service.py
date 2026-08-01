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

import config


def send_instagram_message(recipient_id, message):
    """Instagram kullanıcısına (IGSID) metin mesajı gönderir.

    Hesap/token/taban adres AKTİF TENANT'ın ayarından okunur (yoksa .env
    fallback). Böylece her tenant kendi Instagram hesabından gönderim yapar.
    Taban adres graph.facebook.com (FB Sayfası) ya da graph.instagram.com olabilir.
    """

    url = (
        f"https://{config.ig_api_base()}/{config.ig_graph_version()}/"
        f"{config.ig_account_id()}/messages"
    )

    headers = {
        "Authorization": f"Bearer {config.ig_access_token()}",
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
