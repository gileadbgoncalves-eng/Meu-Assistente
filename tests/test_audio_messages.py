"""Testes para o processamento de mensagens de voz e áudio no WhatsApp (v0.2.5)."""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.whatsapp import WhatsAppClient
from app.db.repository import EventRepository
from app.services.assistant import AssistantService


def test_whatsapp_webhook_parser_handles_audio():
    """Testa se o parser do WhatsApp identifica e extrai mensagens do tipo audio."""
    client = WhatsAppClient()
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "5511999998888", "profile": {"name": "Ana Clara"}}],
                            "messages": [
                                {
                                    "from": "5511999998888",
                                    "id": "wamid.AUDIO_123456",
                                    "type": "audio",
                                    "audio": {
                                        "id": "media_audio_id_999",
                                        "mime_type": "audio/ogg; codecs=opus",
                                    },
                                    "timestamp": "1730000000",
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }

    messages = client.parse_webhook_payload(payload)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.message_type == "audio"
    assert msg.media_id == "media_audio_id_999"
    assert msg.mime_type == "audio/ogg; codecs=opus"
    assert msg.sender_phone == "5511999998888"
    assert msg.sender_name == "Ana Clara"


@pytest.mark.asyncio
async def test_whatsapp_download_media_fallback():
    """Testa download_media com retorno gracioso sem token de acesso."""
    client = WhatsAppClient(access_token="")
    data = await client.download_media("some_media_id")
    assert isinstance(data, bytes)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_assistant_process_audio_message_flow(db_session: AsyncSession, mock_interpreter):
    """Testa fluxo completo de process_audio_message no AssistantService."""
    from app.ai.schemas import AIInterpretationResult
    from app.domain.enums import ConfidenceLevel, IntentType

    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Dentista",
            start_datetime="2026-10-27T14:00:00",
            end_datetime="2026-10-27T15:00:00",
            location_raw="Av. Paulista, 1000",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    service = AssistantService(
        session=db_session,
        interpreter=mock_interpreter,
    )

    audio_bytes = b"MOCK_OGG_AUDIO_BYTES_VOICE_NOTE"
    phone = "5511988887777"
    anchor = datetime(2026, 10, 26, 10, 0)

    reply = await service.process_audio_message(
        phone_number=phone,
        audio_bytes=audio_bytes,
        mime_type="audio/ogg",
        user_name="Carlos",
        anchor_datetime=anchor,
    )

    assert "🎤 *Áudio recebido:*" in reply
    assert '"Dentista"' in reply
    assert "✅ Compromisso agendado com sucesso!" in reply

    # Verifica se o evento foi gravado no banco de dados
    event_repo = EventRepository(db_session)
    events = await event_repo.list_events_in_range(
        owner_user_id=1,
        start_time=datetime(2026, 10, 27, 0, 0),
        end_time=datetime(2026, 10, 27, 23, 59),
    )
    assert len(events) == 1
    assert "Dentista" in events[0].title
