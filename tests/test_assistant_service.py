"""Testes end-to-end do orquestrador AssistantService."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ai.schemas import AIInterpretationResult
from app.db.repository import EventRepository
from app.domain.enums import ConfidenceLevel, EventStatus, IntentType
from app.engine.state_machine import ConversationStateManager
from app.services.assistant import AssistantService


@pytest.mark.asyncio
async def test_assistant_direct_create_no_conflict(db_session, mock_interpreter):
    """Testa criação direta de evento sem conflito na agenda."""
    anchor = datetime(2026, 10, 21, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    # Configura resposta mock da IA
    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Dentista",
            start_datetime="2026-10-22T14:00:00",
            end_datetime="2026-10-22T15:00:00",
            location_raw="Av. Paulista, 1000",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    service = AssistantService(
        session=db_session,
        interpreter=mock_interpreter,
    )

    response = await service.process_message(
        phone_number="5511999990001",
        raw_text="Dentista amanhã às 14h na Paulista",
        anchor_datetime=anchor,
    )

    assert "✅ Compromisso agendado com sucesso!" in response
    assert "Dentista" in response
    assert "Av. Paulista, 1000" in response

    # Verifica no banco se foi gravado
    event_repo = EventRepository(db_session)
    events = await event_repo.list_events_in_range(
        owner_user_id=1,
        start_time=datetime(2026, 10, 22, 0, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
        end_time=datetime(2026, 10, 22, 23, 59, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )
    assert len(events) == 1
    assert events[0].title == "Dentista"


@pytest.mark.asyncio
async def test_assistant_clarification_on_missing_time(db_session, mock_interpreter):
    """Testa que se o horário faltar, o assistente pede esclarecimento sem gravar nada."""
    anchor = datetime(2026, 10, 21, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Consulta Médica",
            start_datetime=None,
            missing_fields=["horário de início"],
            ambiguity_questions=["Qual é o horário da sua consulta médica?"],
            confidence=ConfidenceLevel.MEDIUM,
        )
    )

    service = AssistantService(session=db_session, interpreter=mock_interpreter)

    response = await service.process_message(
        phone_number="5511999990001",
        raw_text="Consulta médica amanhã",
        anchor_datetime=anchor,
    )

    assert "Qual é o horário da sua consulta médica?" in response

    # Garante que nenhum evento foi criado
    event_repo = EventRepository(db_session)
    events = await event_repo.list_events_in_range(
        owner_user_id=1,
        start_time=anchor - timedelta(days=5),
        end_time=anchor + timedelta(days=5),
    )
    assert len(events) == 0


@pytest.mark.asyncio
async def test_assistant_conflict_and_confirmation_flow(db_session, mock_interpreter):
    """Testa fluxo de conflito com confirmação do usuário ('sim, pode agendar')."""
    tz = ZoneInfo("America/Sao_Paulo")
    anchor = datetime(2026, 10, 21, 10, 0, tzinfo=tz)

    state_manager = ConversationStateManager()
    service = AssistantService(
        session=db_session,
        interpreter=mock_interpreter,
        state_manager=state_manager,
    )

    # 1. Cria primeiro evento: 14h às 15h
    event_repo = EventRepository(db_session)
    user = await service.user_repo.get_or_create("5511999990001", "Bernardo")
    await event_repo.create_event(
        owner_user_id=user.id,
        title="Reunião Pré-existente",
        start_time=datetime(2026, 10, 22, 14, 0, tzinfo=tz),
        end_time=datetime(2026, 10, 22, 15, 0, tzinfo=tz),
        status=EventStatus.CONFIRMED.value,
    )

    # 2. Usuário tenta agendar compromisso colidindo: 14h30 às 15h30
    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Café com Cliente",
            start_datetime="2026-10-22T14:30:00",
            end_datetime="2026-10-22T15:30:00",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    warning_response = await service.process_message(
        phone_number="5511999990001",
        raw_text="Café com cliente amanhã às 14h30",
        anchor_datetime=anchor,
    )

    assert "Aviso de conflito" in warning_response
    assert "Reunião Pré-existente" in warning_response
    # Estado pendente deve ter sido criado
    assert state_manager.get_pending(user.id) is not None

    # 3. Usuário confirma: "sim, pode agendar mesmo assim"
    confirm_response = await service.process_message(
        phone_number="5511999990001",
        raw_text="sim, pode agendar",
        anchor_datetime=anchor,
    )

    assert "Confirmado!" in confirm_response
    assert "Café com Cliente" in confirm_response
    # Estado pendente deve estar limpo
    assert state_manager.get_pending(user.id) is None

    # Verifica se os 2 eventos existem agora no banco
    all_events = await event_repo.list_events_in_range(
        owner_user_id=user.id,
        start_time=datetime(2026, 10, 22, 0, 0, tzinfo=tz),
        end_time=datetime(2026, 10, 22, 23, 59, tzinfo=tz),
    )
    assert len(all_events) == 2


@pytest.mark.asyncio
async def test_assistant_conflict_and_denial_flow(db_session, mock_interpreter):
    """Testa fluxo de conflito recusado pelo usuário ('não, cancela')."""
    tz = ZoneInfo("America/Sao_Paulo")
    anchor = datetime(2026, 10, 21, 10, 0, tzinfo=tz)

    state_manager = ConversationStateManager()
    service = AssistantService(
        session=db_session,
        interpreter=mock_interpreter,
        state_manager=state_manager,
    )

    user = await service.user_repo.get_or_create("5511999990001", "Bernardo")
    await service.event_repo.create_event(
        owner_user_id=user.id,
        title="Almoço",
        start_time=datetime(2026, 10, 22, 12, 0, tzinfo=tz),
        end_time=datetime(2026, 10, 22, 13, 0, tzinfo=tz),
        status=EventStatus.CONFIRMED.value,
    )

    # Nova tentativa colidindo
    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Almoço de Negócios",
            start_datetime="2026-10-22T12:30:00",
            end_datetime="2026-10-22T13:30:00",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    await service.process_message(
        phone_number="5511999990001",
        raw_text="Almoço de negócios amanhã 12h30",
        anchor_datetime=anchor,
    )

    # Usuário recusa
    denial_response = await service.process_message(
        phone_number="5511999990001",
        raw_text="não, deixa quieto",
        anchor_datetime=anchor,
    )

    assert "cancelada e nenhuma alteração foi feita" in denial_response
    assert state_manager.get_pending(user.id) is None

    # Continua apenas 1 evento no banco
    events = await service.event_repo.list_events_in_range(
        owner_user_id=user.id,
        start_time=datetime(2026, 10, 22, 0, 0, tzinfo=tz),
        end_time=datetime(2026, 10, 22, 23, 59, tzinfo=tz),
    )
    assert len(events) == 1


@pytest.mark.asyncio
async def test_assistant_query_events(db_session, mock_interpreter):
    """Testa consulta da agenda com formatação amigável."""
    tz = ZoneInfo("America/Sao_Paulo")
    anchor = datetime(2026, 10, 21, 10, 0, tzinfo=tz)

    service = AssistantService(session=db_session, interpreter=mock_interpreter)
    user = await service.user_repo.get_or_create("5511999990001", "Bernardo")

    # Adiciona compromisso
    await service.event_repo.create_event(
        owner_user_id=user.id,
        title="Reunião de Equipe",
        start_time=datetime(2026, 10, 21, 15, 0, tzinfo=tz),
        end_time=datetime(2026, 10, 21, 16, 0, tzinfo=tz),
        status=EventStatus.CONFIRMED.value,
    )

    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.QUERY_EVENTS,
            start_datetime="2026-10-21T00:00:00",
            end_datetime="2026-10-21T23:59:59",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    query_response = await service.process_message(
        phone_number="5511999990001",
        raw_text="O que tenho hoje?",
        anchor_datetime=anchor,
    )

    assert "Seus compromissos encontrados (1):" in query_response
    assert "Reunião de Equipe" in query_response
    assert "15h às 16h" in query_response


@pytest.mark.asyncio
async def test_assistant_idempotency_duplicate_handling(db_session, mock_interpreter):
    """Testa rejeição de mensagem com chave de idempotência duplicada."""
    anchor = datetime(2026, 10, 21, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Treino",
            start_datetime="2026-10-23T07:00:00",
            end_datetime="2026-10-23T08:00:00",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    service = AssistantService(session=db_session, interpreter=mock_interpreter)

    # Primeira chamada com chave de mensagem
    resp1 = await service.process_message(
        phone_number="5511999990001",
        raw_text="Treino sexta às 7h",
        idempotency_key="wamid_12345",
        anchor_datetime=anchor,
    )
    assert "✅ Compromisso agendado" in resp1

    # Segunda chamada com MESMA chave
    resp2 = await service.process_message(
        phone_number="5511999990001",
        raw_text="Treino sexta às 7h",
        idempotency_key="wamid_12345",
        anchor_datetime=anchor,
    )
    assert "já foi processada anteriormente" in resp2
