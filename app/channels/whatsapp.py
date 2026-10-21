"""Adaptador de integração com a WhatsApp Cloud API (Meta Graph API)."""

import hashlib
import hmac
import logging
from typing import Optional
import httpx
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


class IncomingWhatsAppMessage(BaseModel):
    """Mensagem de texto extraída do payload de webhook da Meta."""
    sender_phone: str = Field(description="Telefone do remetente (formato internacional, ex: 5511999998888)")
    sender_name: str = Field(default="Usuário", description="Nome de exibição no WhatsApp se disponível")
    message_id: str = Field(description="Identificador único da mensagem atribuído pela Meta (wamid)")
    text: str = Field(description="Texto da mensagem enviada")
    timestamp: Optional[str] = Field(default=None, description="Timestamp da mensagem")


class WhatsAppClient:
    """Cliente assíncrono para validação e despacho de mensagens via WhatsApp Cloud API."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        api_version: Optional[str] = None,
    ):
        self.access_token = access_token or settings.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID
        self.app_secret = app_secret or settings.WHATSAPP_APP_SECRET
        self.api_version = api_version or settings.WHATSAPP_API_VERSION

    def verify_signature(self, payload_bytes: bytes, signature_header: Optional[str]) -> bool:
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
        """Extrai as mensagens textuais do payload JSON padrão da Meta Cloud API."""
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
                        # Processa apenas mensagens do tipo texto
                        if msg.get("type") == "text":
                            sender = msg.get("from")
                            text_body = msg.get("text", {}).get("body", "").strip()
                            wamid = msg.get("id")

                            if sender and text_body and wamid:
                                name = contacts.get(sender, "Usuário")
                                extracted.append(
                                    IncomingWhatsAppMessage(
                                        sender_phone=sender,
                                        sender_name=name,
                                        message_id=wamid,
                                        text=text_body,
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
                if response.status_code in (200, 201):
                    logger.info("Mensagem enviada com sucesso para %s via WhatsApp API", recipient_phone)
                    return True
                else:
                    logger.error("Falha ao enviar mensagem pelo WhatsApp API: %s %s", response.status_code, response.text)
                    return False
        except Exception as exc:
            logger.error("Exceção ao comunicar com a WhatsApp Graph API: %s", exc)
            return False
