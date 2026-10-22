"""Testes para regressão das falhas de interpretação/data e subsistema de lembretes."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest

from app.channels.simulator import OfflineSimulatorMock
from app.services.assistant import AssistantService
from app.ai.schemas import AIInterpretationResult
from app.domain.enums import IntentType, ConfidenceLevel, EventStatus
from app.db.repository import ReminderRepository, EventRepository, UserRepository
from app.engine.validator import DeterministicValidator
from app.engine.state_machine import ConversationStateManager


# ==============================================================================
# 1. TESTES DE REGRESSÃO: FALHAS OBSERVADAS NO SIMULADOR
# ==============================================================================

@pytest.mark.asyncio
async def test_regression_agendar_compromisso_intent():
    """Garante que 'AGENDAR COMPROMISSO' é classificado como CREATE_EVENT com campos faltantes,
    NUNCA como query_events.
    """
    mock = OfflineSimulatorMock()
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    # Variação em maiúsculas
    res1 = await mock.interpret("AGENDAR COMPROMISSO", anchor)
    assert res1.intent == IntentType.CREATE_EVENT
    assert "data" in res1.missing_fields
    assert "horário de início" in res1.missing_fields
    assert len(res1.ambiguity_questions) > 0

    # Variação em minúsculas com verbo
    res2 = await mock.interpret("quero agendar um compromisso", anchor)
    assert res2.intent == IntentType.CREATE_EVENT
    assert "data" in res2.missing_fields
    assert "horário de início" in res2.missing_fields


@pytest.mark.asyncio
async def test_regression_agendar_compromisso_in_assistant(db_session):
    """Testa fluxo completo no AssistantService para 'AGENDAR COMPROMISSO'.
    Deve perguntar dados e NÃO dizer 'Você não tem nenhum compromisso marcado'.
    """
    mock = OfflineSimulatorMock()
    service = AssistantService(session=db_session, interpreter=mock)
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    response = await service.process_message(
        phone_number="5511999990001",
        raw_text="AGENDAR COMPROMISSO",
        anchor_datetime=anchor,
    )

    assert "Você não tem nenhum compromisso marcado" not in response
    assert "o que você gostaria de agendar" in response.lower() or "para qual data" in response.lower()


@pytest.mark.asyncio
async def test_regression_explicit_date_and_morning_time():
    """Garante que 'médico dia 30/10/2026 às 07:00 da manhã.' é interpretado na data e hora
    exatas especificadas, e NÃO sobrescrito pela data corrente/fallback (22/10 às 14h).
    """
    mock = OfflineSimulatorMock()
    anchor = datetime(2026, 10, 22, 14, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    res = await mock.interpret("médico dia 30/10/2026 às 07:00 da manhã.", anchor)

    assert res.intent == IntentType.CREATE_EVENT
    assert res.title == "Consulta Médica"
    assert res.start_datetime.startswith("2026-10-30T07:00:00")
    assert res.end_datetime.startswith("2026-10-30T08:00:00")
    assert len(res.missing_fields) == 0


@pytest.mark.asyncio
async def test_regression_explicit_date_in_assistant(db_session):
    """Testa fluxo completo de agendamento no AssistantService com 'médico dia 30/10/2026 às 07:00 da manhã.'"""
    mock = OfflineSimulatorMock()
    service = AssistantService(session=db_session, interpreter=mock)
    anchor = datetime(2026, 10, 22, 14, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    response = await service.process_message(
        phone_number="5511999990001",
        raw_text="médico dia 30/10/2026 às 07:00 da manhã.",
        anchor_datetime=anchor,
    )

    assert "✅ Compromisso agendado com sucesso!" in response
    assert "30/10/2026" in response
    assert "07h" in response
    assert "22/10/2026" not in response

    # Verifica no banco
    event_repo = EventRepository(db_session)
    events = await event_repo.list_events_in_range(
        owner_user_id=1,
        start_time=datetime(2026, 10, 30, 0, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
        end_time=datetime(2026, 10, 30, 23, 59, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )
    assert len(events) == 1
    assert events[0].title == "Consulta Médica"
    assert events[0].start_time.day == 30
    assert events[0].start_time.hour == 7


# ==============================================================================
# 2. TESTES DO SUBSISTEMA DE LEMBRETES (REMINDERS)
# ==============================================================================

@pytest.mark.asyncio
async def test_relative_reminder_interpretation():
    """Testa extração de antecedência de lembrete (ex: 'me lembra 2h antes')."""
    mock = OfflineSimulatorMock()
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    res = await mock.interpret("Dentista amanhã às 15h, me lembra 2h antes", anchor)
    assert res.intent == IntentType.CREATE_EVENT
    assert res.reminder_minutes_before == 120


@pytest.mark.asyncio
async def test_event_creation_auto_schedules_default_reminder(db_session, mock_interpreter):
    """Garante que ao criar evento sem especificar lembrete, o sistema agenda o lembrete
    padrão (60 min antes do evento) e inclui na mensagem de resposta.
    """
    tz = ZoneInfo("America/Sao_Paulo")
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=tz)

    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Dentista",
            start_datetime="2026-10-30T10:00:00",
            end_datetime="2026-10-30T11:00:00",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    service = AssistantService(session=db_session, interpreter=mock_interpreter)
    response = await service.process_message(
        phone_number="5511999990001",
        raw_text="Dentista dia 30/10 às 10h",
        anchor_datetime=anchor,
    )

    assert "✅ Compromisso agendado com sucesso!" in response
    assert "🔔 Lembrete configurado para" in response
    assert "30/10/2026 às 09h" in response
    assert "1h antes" in response

    # Valida no banco de dados
    reminder_repo = ReminderRepository(db_session)
    reminders = await reminder_repo.list_by_user(owner_user_id=1)
    assert len(reminders) == 1
    rem = reminders[0]
    assert rem.status == "pending"
    assert rem.event_id is not None
    assert "Dentista" in rem.message_text
    # 10:00 - 60 min = 09:00
    assert rem.send_at.replace(tzinfo=None) == datetime(2026, 10, 30, 9, 0)


@pytest.mark.asyncio
async def test_event_creation_with_custom_reminder_offset(db_session, mock_interpreter):
    """Garante que antecedência personalizada (ex: 120 min) é respeitada."""
    tz = ZoneInfo("America/Sao_Paulo")
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=tz)

    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Voo para Brasília",
            start_datetime="2026-11-05T14:00:00",
            end_datetime="2026-11-05T15:30:00",
            reminder_minutes_before=120,
            confidence=ConfidenceLevel.HIGH,
        )
    )

    service = AssistantService(session=db_session, interpreter=mock_interpreter)
    response = await service.process_message(
        phone_number="5511999990001",
        raw_text="Voo para Brasília dia 05/11 às 14h, me avisa 2 horas antes",
        anchor_datetime=anchor,
    )

    assert "🔔 Lembrete configurado para" in response
    assert "05/11/2026 às 12h" in response
    assert "2h antes" in response

    reminder_repo = ReminderRepository(db_session)
    reminders = await reminder_repo.list_by_user(owner_user_id=1)
    assert len(reminders) == 1
    assert reminders[0].send_at.replace(tzinfo=None) == datetime(2026, 11, 5, 12, 0)


@pytest.mark.asyncio
async def test_standalone_reminder_flow(db_session, mock_interpreter):
    """Garante fluxo de lembrete avulso (intent=REMINDER_REQUEST)."""
    tz = ZoneInfo("America/Sao_Paulo")
    anchor = datetime(2026, 10, 22, 14, 0, tzinfo=tz)

    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.REMINDER_REQUEST,
            reminder_message="Tomar remédio da pressão",
            reminder_trigger_datetime="2026-10-22T15:00:00",
            confidence=ConfidenceLevel.HIGH,
        )
    )

    service = AssistantService(session=db_session, interpreter=mock_interpreter)
    response = await service.process_message(
        phone_number="5511999990001",
        raw_text="me lembra de tomar remédio daqui a 1h",
        anchor_datetime=anchor,
    )

    assert "🔔 Lembrete agendado com sucesso!" in response
    assert "Tomar remédio da pressão" in response
    assert "22/10/2026 às 15h" in response

    reminder_repo = ReminderRepository(db_session)
    reminders = await reminder_repo.list_by_user(owner_user_id=1)
    assert len(reminders) == 1
    rem = reminders[0]
    assert rem.event_id is None
    assert rem.message_text == "Tomar remédio da pressão"
    assert rem.send_at.replace(tzinfo=None) == datetime(2026, 10, 22, 15, 0)


@pytest.mark.asyncio
async def test_offline_mock_standalone_reminder_parsing():
    """Valida o analisador heurístico offline para lembrete avulso."""
    mock = OfflineSimulatorMock()
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    res = await mock.interpret("me lembra de tomar antibiótico daqui a 1 hora", anchor)
    assert res.intent == IntentType.REMINDER_REQUEST
    assert res.reminder_message is not None
    assert "tomar antibiótico" in res.reminder_message.lower()
    assert res.reminder_trigger_datetime is not None
    parsed_dt = datetime.fromisoformat(res.reminder_trigger_datetime)
    assert parsed_dt.hour == 11


@pytest.mark.asyncio
async def test_reminder_repository_crud_and_link(db_session):
    """Testa criação e busca de lembretes vinculados a eventos e usuários."""
    tz = ZoneInfo("America/Sao_Paulo")
    user_repo = UserRepository(db_session)
    event_repo = EventRepository(db_session)
    reminder_repo = ReminderRepository(db_session)

    user = await user_repo.get_or_create("5511999997777", "Carlos")
    assert user.default_reminder_minutes == 60

    event = await event_repo.create_event(
        owner_user_id=user.id,
        title="Palestra Tech",
        start_time=datetime(2026, 11, 10, 19, 0, tzinfo=tz),
        end_time=datetime(2026, 11, 10, 20, 30, tzinfo=tz),
    )

    # Cria lembrete vinculado ao evento
    rem1 = await reminder_repo.create_reminder(
        owner_user_id=user.id,
        recipient_phone=user.phone_number,
        message_text=f"Lembrete: {event.title}",
        send_at=datetime(2026, 11, 10, 18, 0, tzinfo=tz),
        event_id=event.id,
    )

    # Cria lembrete avulso
    rem2 = await reminder_repo.create_reminder(
        owner_user_id=user.id,
        recipient_phone=user.phone_number,
        message_text="Pagar conta de luz",
        send_at=datetime(2026, 11, 10, 10, 0, tzinfo=tz),
    )

    # Listagem por usuário
    user_reminders = await reminder_repo.list_by_user(user.id)
    assert len(user_reminders) == 2

    # Listagem por evento
    event_reminders = await reminder_repo.list_by_event(event.id)
    assert len(event_reminders) == 1
    assert event_reminders[0].id == rem1.id


# ==============================================================================
# 3. TESTES DE CONTEXTO MULTI-TURNO E RESOLUÇÃO ANAFÓRICA
# ==============================================================================

@pytest.mark.asyncio
async def test_offline_mock_anaphoric_reminder():
    """Valida extração pelo mock de lembrete anafórico com antecedência relativa."""
    mock = OfflineSimulatorMock()
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))

    res = await mock.interpret("me lembre dessa consulta 1 dias antes", anchor)
    assert res.intent == IntentType.REMINDER_REQUEST
    assert res.reminder_minutes_before == 1440
    assert res.event_reference is not None
    assert "dessa consulta" in res.event_reference.lower()


@pytest.mark.asyncio
async def test_multiturn_anaphoric_reminder_resolution(db_session):
    """Garante que na sequência de 2 turnos:
    Turno 1: 'consulta para o Bernardo dia 30/10 ás 07:00' -> cria evento
    Turno 2: 'me lembre dessa consulta 1 dias antes' -> resolve o evento anterior,
             calcula 29/10/2026 às 07:00 e persiste o lembrete vinculado.
    """
    mock = OfflineSimulatorMock()
    service = AssistantService(session=db_session, interpreter=mock)
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    phone = "5511999990001"

    # Turno 1: Agendamento do evento
    resp1 = await service.process_message(
        phone_number=phone,
        raw_text="consulta para o Bernardo dia 30/10 ás 07:00",
        anchor_datetime=anchor,
    )
    assert "✅ Compromisso agendado com sucesso!" in resp1
    assert "30/10/2026" in resp1
    assert "07h" in resp1

    # Turno 2: Lembrete relativo referenciando o evento anterior
    resp2 = await service.process_message(
        phone_number=phone,
        raw_text="me lembre dessa consulta 1 dias antes",
        anchor_datetime=anchor,
    )
    assert "🔔 Lembrete configurado com sucesso para o compromisso" in resp2
    assert "29/10/2026 às 07h" in resp2
    assert "1 dia antes" in resp2

    # Valida no banco de dados
    user_repo = UserRepository(db_session)
    user = await user_repo.get_by_phone(phone)
    assert user is not None

    reminder_repo = ReminderRepository(db_session)
    reminders = await reminder_repo.list_by_user(user.id)
    assert len(reminders) >= 2

    target_rem = next(
        (r for r in reminders if r.send_at.replace(tzinfo=None) == datetime(2026, 10, 29, 7, 0)),
        None
    )
    assert target_rem is not None, "Lembrete de 1 dia antes (29/10 às 07h) não encontrado"
    assert target_rem.event_id is not None


@pytest.mark.asyncio
async def test_multiturn_anaphoric_reminder_with_mock_interpreter(db_session, mock_interpreter):
    """Testa o AssistantService simulando a resposta estruturada exata que o Gemini produz
    para anáfora ('dessa consulta') e antecedência de 1440 minutos.
    """
    anchor = datetime(2026, 10, 22, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    phone = "5511999990002"
    service = AssistantService(session=db_session, interpreter=mock_interpreter)

    # Turno 1: Gemini extrai criação do evento
    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.CREATE_EVENT,
            title="Consulta Médica com Dr. Silva",
            start_datetime="2026-10-30T07:00:00",
            end_datetime="2026-10-30T08:00:00",
            confidence=ConfidenceLevel.HIGH,
        )
    )
    resp1 = await service.process_message(
        phone_number=phone,
        raw_text="consulta com Dr Silva dia 30/10 as 07:00",
        anchor_datetime=anchor,
    )
    assert "✅ Compromisso agendado com sucesso!" in resp1
    assert "Consulta Médica com Dr. Silva" in resp1

    # Turno 2: Gemini reconhece pedido de lembrete com anáfora
    mock_interpreter.set_response(
        AIInterpretationResult(
            intent=IntentType.REMINDER_REQUEST,
            event_reference="dessa consulta",
            reminder_minutes_before=1440,
            confidence=ConfidenceLevel.HIGH,
        )
    )
    resp2 = await service.process_message(
        phone_number=phone,
        raw_text="me lembre dessa consulta 1 dia antes",
        anchor_datetime=anchor,
    )
    assert "🔔 Lembrete configurado com sucesso para o compromisso 'Consulta Médica com Dr. Silva'" in resp2
    assert "29/10/2026 às 07h" in resp2
    assert "1 dia antes" in resp2


