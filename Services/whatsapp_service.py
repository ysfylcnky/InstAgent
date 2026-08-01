import requests
import config

def send_whatsapp_message(to_number, message):

    # Mağaza bildirimi AKTİF TENANT'ın WhatsApp bilgileriyle gönderilir.
    url = (
        f"https://graph.facebook.com/v23.0/"
        f"{config.whatsapp_phone_number_id()}/messages"
    )

    headers = {
        "Authorization":
            f"Bearer {config.whatsapp_access_token()}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {
            "body": message
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload
    )

    print("STATUS:", response.status_code)
    print("RESPONSE:", response.text)
