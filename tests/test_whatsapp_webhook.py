"""Testes da camada de webhook da WhatsApp Cloud API e adaptadores."""

import hashlib
import hmac
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.config import settings
from app.channels.whatsapp import WhatsAppClient
from app.adapters.google_calendar import GoogleCalendarAdapter
from app.adapters.google_maps import GoogleMapsAdapter


@pytest.mark.asyncio
async def test_webhook_handshake_success():
    """Testa handshake de verificação com token correto da Meta."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
                "hub.challenge": "1158201444",
            },
        )
        assert response.status_code == 200
        assert response.text == "1158201444"


@pytest.mark.asyncio
async def test_webhook_handshake_invalid_token():
    """Testa rejeição do handshake quando o token for incorreto."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "token_errado",
                "hub.challenge": "1158201444",
            },
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_whatsapp_signature_verification():
    """Testa validação criptográfica HMAC SHA-256 da assinatura da Meta."""
    client = WhatsAppClient(app_secret="meu_segredo_de_teste")
    payload = b'{"object":"whatsapp_business_account"}'

    # Gera assinatura válida
    mac = hmac.new(b"meu_segredo_de_teste", msg=payload, digestmod=hashlib.sha256)
    valid_sig = f"sha256={mac.hexdigest()}"

    assert client.verify_signature(payload, valid_sig) is True
    assert client.verify_signature(payload, "sha256=assinatura_invalida") is False
    assert client.verify_signature(payload, None) is False


def test_whatsapp_payload_parser():
    """Testa parse de payload padrão da Meta Cloud API."""
    client = WhatsAppClient()
    sample_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1555000", "phone_number_id": "1000"},
                            "contacts": [{"profile": {"name": "Bernardo"}, "wa_id": "5511999998888"}],
                            "messages": [
                                {
                                    "from": "5511999998888",
                                    "id": "wamid.HBgLNTUxMTk5...",
                                    "timestamp": "1730000000",
                                    "text": {"body": "Consulta quinta às 14h"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    messages = client.parse_webhook_payload(sample_payload)
    assert len(messages) == 1
    assert messages[0].sender_phone == "5511999998888"
    assert messages[0].sender_name == "Bernardo"
    assert messages[0].text == "Consulta quinta às 14h"
    assert messages[0].message_id == "wamid.HBgLNTUxMTk5..."


@pytest.mark.asyncio
async def test_adapters_google_calendar_and_maps_graceful_fallbacks():
    """Testa resiliência dos adaptadores externos com fallback gracioso."""
    from datetime import datetime, timezone

    # 1. Google Calendar Adapter
    gcal = GoogleCalendarAdapter()
    gcal_event = await gcal.create_event({
        "title": "Alinhamento",
        "start_time": datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc),
        "end_time": datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc),
        "participants": [{"name": "Pedro"}],
    })
    assert gcal_event.provider == "google_calendar"
    assert "gcal_" in gcal_event.external_id
    assert gcal_event.summary == "Alinhamento"

    # 2. Google Maps Adapter
    gmaps = GoogleMapsAdapter()
    estimate = await gmaps.estimate_travel_time(
        origin="Av. Paulista, 1000",
        destination="Rua Funchal, 200",
        departure_time=datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc),
    )
    assert estimate.duration_minutes > 0
    assert estimate.is_approximate is True
    assert "Google Maps Platform não configurada" in (estimate.warning or "")
