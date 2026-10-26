"""Testes unitários e de integração para o GoogleCalendarAdapter e propagação no AssistantService."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.adapters.google_calendar import GoogleCalendarAdapter
from app.ai.schemas import AIInterpretationResult
from app.domain.entities import Event
from app.domain.enums import ConfidenceLevel, IntentType
from app.services.assistant import AssistantService


@pytest.mark.asyncio
async def test_gcal_adapter_fallback_mode():
    """Testa modo de fallback gracioso sem credenciais."""
    adapter = GoogleCalendarAdapter(credentials_file="", credentials_json="", access_token="")
    event_data = {
        "title": "Reunião de Diretoria",
        "start_time": datetime(2026, 10, 26, 14, 0, tzinfo=UTC),
        "end_time": datetime(2026, 10, 26, 15, 0, tzinfo=UTC),
        "location": "Sala 101",
    }
    res = await adapter.create_event(event_data)
    assert res.provider == "google_calendar"
    assert "gcal_" in res.external_id
    assert res.summary == "Reunião de Diretoria"
    assert res.location == "Sala 101"

    # Teste de update
    upd = await adapter.update_event(res.external_id, {"title": "Reunião Remarcada"})
    assert upd.external_id == res.external_id
    assert upd.summary == "Reunião Remarcada"

    # Teste de delete
    del_ok = await adapter.delete_event(res.external_id)
    assert del_ok is True


@pytest.mark.asyncio
async def test_gcal_adapter_with_event_entity():
    """Testa create_event passando entidade Event de domínio."""
    adapter = GoogleCalendarAdapter()
    event = Event(
        owner_user_id=1,
        title="Alinhamento Semanal",
        start_time=datetime(2026, 10, 27, 9, 0, tzinfo=UTC),
        end_time=datetime(2026, 10, 27, 10, 0, tzinfo=UTC),
        location="Av Paulista, 1000",
    )
    res = await adapter.create_event(event)
    assert "gcal_" in res.external_id
    assert res.summary == "Alinhamento Semanal"


@pytest.mark.asyncio
async def test_gcal_adapter_network_failure_resilience():
    """Testa resiliência quando a API do Google retorna erro de conexão."""
    adapter = GoogleCalendarAdapter(access_token="fake_token_123")
    assert adapter._is_authenticated is True

    # Simula erro de conexão no httpx.AsyncClient.post
    with patch("httpx.AsyncClient.post", side_effect=Exception("Connection refused")):
        res = await adapter.create_event({
            "title": "Evento Resiliente",
            "start_time": datetime.now(UTC),
            "end_time": datetime.now(UTC),
        })
        assert "gcal_" in res.external_id
        assert res.summary == "Evento Resiliente"


@pytest.mark.asyncio
async def test_assistant_service_propagates_to_google_calendar(db_session, mock_interpreter):
    """Testa se o AssistantService cria evento e sincroniza o external_calendar_id."""
    mock_adapter = AsyncMock()
    mock_adapter.create_event.return_value = AsyncMock(external_id="gcal_test_sync_999")
    mock_adapter.update_event.return_value = True
    mock_adapter.delete_event.return_value = True

    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Reunião com Diretoria",
            start_datetime="2026-10-26T15:00:00",
            end_datetime="2026-10-26T16:00:00",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    service = AssistantService(
        session=db_session,
        interpreter=mock_interpreter,
        calendar_adapter=mock_adapter,
    )

    phone = "5511999990001"
    anchor = datetime(2026, 10, 26, 10, 0)
    reply = await service.process_message(
        phone_number=phone,
        raw_text="reunião com diretoria amanhã às 15h",
        user_name="Diretor",
        anchor_datetime=anchor,
    )
    assert "Compromisso agendado com sucesso" in reply
    mock_adapter.create_event.assert_called_once()


@pytest.mark.asyncio
async def test_google_calendar_adapter_update_and_delete_fallback():
    """Valida métodos update_event e delete_event em modo fallback local."""
    adapter = GoogleCalendarAdapter(credentials_json=None)
    ev = Event(
        id=1,
        owner_user_id=1,
        title="Alinhamento",
        start_time=datetime(2026, 10, 26, 14, 0),
        end_time=datetime(2026, 10, 26, 15, 0),
    )
    gcal_id = await adapter.create_event(ev)
    assert gcal_id is not None

    # Atualização
    update_ok = await adapter.update_event(gcal_id, {"summary": "Alinhamento Atualizado"})
    assert update_ok is not None

    # Deleção
    del_ok = await adapter.delete_event(gcal_id)
    assert del_ok is True


@pytest.mark.asyncio
async def test_google_calendar_adapter_list_events_fallback():
    """Valida list_events em modo fallback gracioso."""
    adapter = GoogleCalendarAdapter(credentials_json=None)
    now = datetime(2026, 10, 26, 10, 0)
    events = await adapter.list_events(now, now + timedelta(days=7))
    assert isinstance(events, list)


def test_google_calendar_adapter_maps_attendees_with_email():
    """Valida que os participantes com e-mail são mapeados para o array attendees do Google."""
    from app.domain.entities import EventParticipant

    adapter = GoogleCalendarAdapter()
    event = Event(
        owner_user_id=1,
        title="Reunião com Parceiros",
        start_time=datetime(2026, 10, 28, 10, 0),
        end_time=datetime(2026, 10, 28, 11, 0),
        participants=[
            EventParticipant(name="Alice Silva", email="alice@empresa.com"),
            EventParticipant(name="Bob Souza", email=None),
        ],
    )
    payload = adapter._convert_to_google_payload(event)
    attendees = payload["attendees"]
    assert len(attendees) == 2
    assert attendees[0] == {"displayName": "Alice Silva", "email": "alice@empresa.com"}
    assert attendees[1] == {"displayName": "Bob Souza"}


