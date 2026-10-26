"""Adaptador de integração com a WhatsApp Cloud API (Meta Graph API)."""

import hashlib
import hmac
import logging

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.domain.formatters import DataSanitizer

logger = logging.getLogger(__name__)


class IncomingWhatsAppMessage(BaseModel):
    """Mensagem de texto ou áudio extraída do payload de webhook da Meta."""
    sender_phone: str = Field(description="Telefone do remetente (formato internacional, ex: 5511999998888)")
    sender_name: str = Field(default="Usuário", description="Nome de exibição no WhatsApp se disponível")
    message_id: str = Field(description="Identificador único da mensagem atribuído pela Meta (wamid)")
    text: str = Field(default="", description="Texto da mensagem enviada")
    message_type: str = Field(default="text", description="Tipo da mensagem: 'text', 'audio', etc.")
    media_id: str | None = Field(default=None, description="ID do arquivo de áudio para download via Graph API")
    mime_type: str | None = Field(default=None, description="Tipo MIME do áudio (ex: audio/ogg; codecs=opus)")
    timestamp: str | None = Field(default=None, description="Timestamp da mensagem")


class WhatsAppClient:
    """Cliente assíncrono para validação e despacho de mensagens via WhatsApp Cloud API."""

    def __init__(
        self,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        app_secret: str | None = None,
        api_version: str | None = None,
    ):
        self.access_token = access_token or settings.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID
        self.app_secret = app_secret or settings.WHATSAPP_APP_SECRET
        self.api_version = api_version or settings.WHATSAPP_API_VERSION

    def verify_signature(self, payload_bytes: bytes, signature_header: str | None) -> bool:
        """Verifica a assinatura criptográfica X-Hub-Signature-256 enviada pela Meta."""
        if not self.app_secret:
            # Se não houver segredo configurado, aceita a mensagem (útil em testes locais)
            return True

        if not signature_header or not signature_header.startswith("sha256="):
            return False

        expected_sig = signature_header[7:]
        mac = hmac.new(self.app_secret.encode("utf-8"), msg=payload_bytes, digestmod=hashlib.sha256)
        computed_sig = mac.hexdigest()

        return hmac.compare_digest(computed_sig, expected_sig)

    def parse_webhook_payload(self, payload: dict) -> list[IncomingWhatsAppMessage]:
        """Extrai as mensagens (texto ou áudio) do payload JSON padrão da Meta Cloud API."""
        extracted: list[IncomingWhatsAppMessage] = []

        try:
            entries = payload.get("entry", [])
            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    contacts = {c.get("wa_id"): c.get("profile", {}).get("name", "Usuário") for c in value.get("contacts", [])}
                    messages = value.get("messages", [])

                    for msg in messages:
                        msg_type = msg.get("type")
                        sender = msg.get("from")
                        wamid = msg.get("id")
                        if not sender or not wamid:
                            continue
                        name = contacts.get(sender, "Usuário")

                        if msg_type == "text":
                            text_body = msg.get("text", {}).get("body", "").strip()
                            if text_body:
                                extracted.append(
                                    IncomingWhatsAppMessage(
                                        sender_phone=sender,
                                        sender_name=name,
                                        message_id=wamid,
                                        text=text_body,
                                        message_type="text",
                                        timestamp=msg.get("timestamp"),
                                    )
                                )
                        elif msg_type == "audio":
                            audio_info = msg.get("audio", {})
                            media_id = audio_info.get("id")
                            mime_type = audio_info.get("mime_type", "audio/ogg")
                            extracted.append(
                                IncomingWhatsAppMessage(
                                    sender_phone=sender,
                                    sender_name=name,
                                    message_id=wamid,
                                    text="",
                                    message_type="audio",
                                    media_id=media_id,
                                    mime_type=mime_type,
                                    timestamp=msg.get("timestamp"),
                                )
                            )
        except Exception as exc:
            logger.error("Erro ao fazer parse do payload do WhatsApp: %s", exc)

        return extracted

    async def send_text_message(self, recipient_phone: str, text: str) -> bool:
        """Envia mensagem de texto via HTTP POST para a Graph API da Meta."""
        if not self.access_token or not self.phone_number_id:
            logger.warning(
                "WhatsApp não configurado (WHATSAPP_ACCESS_TOKEN ou PHONE_NUMBER_ID ausentes). Mensagem simulada: %s",
                text,
            )
            return False

        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, headers=headers, json=body)
                masked_phone = DataSanitizer.mask_phone(recipient_phone)
                if response.status_code in (200, 201):
                    logger.info("Mensagem enviada com sucesso para %s via WhatsApp API", masked_phone)
                    return True
                else:
                    logger.error("Falha ao enviar mensagem pelo WhatsApp API para %s: %s %s", masked_phone, response.status_code, response.text)
                    return False
        except Exception as exc:
            logger.error("Exceção ao comunicar com a WhatsApp Graph API: %s", exc)
            return False

    async def download_media(self, media_id: str) -> bytes:
        """Consulta a URL da mídia na Graph API da Meta e baixa os bytes do arquivo (ex: áudio OGG/Opus)."""
        if not self.access_token:
            logger.info("WhatsApp sem credenciais: simulando download de mídia %s", media_id)
            return b"MOCK_OGG_AUDIO_BYTES_VOICE_NOTE"

        try:
            url = f"https://graph.facebook.com/{self.api_version}/{media_id}"
            headers = {"Authorization": f"Bearer {self.access_token}"}
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    media_url = res.json().get("url")
                    if media_url:
                        media_res = await client.get(media_url, headers=headers)
                        if media_res.status_code == 200:
                            return media_res.content
            logger.warning("Falha ao obter URL da mídia %s na Meta Graph API", media_id)
        except Exception as exc:
            logger.error("Erro ao baixar mídia %s: %s", media_id, exc)

        return b"MOCK_OGG_AUDIO_BYTES_VOICE_NOTE"
