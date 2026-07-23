"""Medya (ses/görsel) indirme + ses transkripsiyonu — Instagram uyarlaması.

WhatsApp'ta medya bir media_id ile gelip ayrı bir uçtan token'la indiriliyordu.
Instagram'da ise webhook, ek dosyayı doğrudan bir URL olarak verir
(message.attachments[].payload.url). Bu yüzden indirme tek bir GET'tir.

Bazı CDN URL'leri süreli/imzalı olur; erişim reddedilirse (401/403) erişim
token'ıyla tekrar denenir. Ses transkripsiyonu WhatsApp projesiyle aynıdır
(OpenAI Whisper), sadece indirme akışı farklıdır.
"""

import io
import requests

from openai import OpenAI

from config import (
    AUDIO_MODEL_NAME,
    OPENAI_API_KEY,
    IG_ACCESS_TOKEN,
)

client = OpenAI(api_key=OPENAI_API_KEY)


def download_attachment(url):
    """Instagram ek dosyasını (ses/görsel) URL'den indirir; içerik baytlarını döner."""

    response = requests.get(url, timeout=30)

    # İmzalı/korumalı URL erişim isterse token'la tekrar dene
    if response.status_code in (401, 403):
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {IG_ACCESS_TOKEN}"},
            timeout=30,
        )

    response.raise_for_status()

    return response.content


def transcribe_audio(audio_bytes):
    """Ses baytlarını Whisper ile Türkçe metne çevirir."""

    audio_file = io.BytesIO(audio_bytes)

    audio_file.name = "voice.ogg"

    transcription = client.audio.transcriptions.create(
        model=AUDIO_MODEL_NAME,
        file=audio_file,
        language="tr",
    )

    return transcription.text.strip()
