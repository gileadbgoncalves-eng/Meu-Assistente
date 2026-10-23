"""Testes unitários e de integração para o ReminderDispatcherService e Agendador de Lembretes."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.whatsapp import WhatsAppClient
from app.db.repository import ReminderRepository, UserRepository
from app.domain.enums import ReminderStatus
from app.services.reminder_dispatcher import ReminderDispatcherService


@pytest.mark.asyncio
async def test_dispatch_due_reminders_success(db_session: AsyncSession):
    """Garante que lembretes vencidos são disparados via WhatsApp e atualizados para 'sent'."""
    tz = ZoneInfo("America/Sao_Paulo")
    user_repo = UserRepository(db_session)
    reminder_repo = ReminderRepository(db_session)

    user = await user_repo.get_or_create("5511999991111", "Lucas")
    now_dt = datetime(2026, 10, 23, 14, 0, tzinfo=tz)

    # Lembrete 1: Vencido (send_at às 13:50) -> DEVE ser disparado
    rem_due = await reminder_repo.create_reminder(
        owner_user_id=user.id,
        recipient_phone=user.phone_number,
        message_text="Lembrete: Reunião às 14h",
        send_at=now_dt - timedelta(minutes=10),
    )

    # Lembrete 2: Futuro (send_at às 15:00) -> NÃO deve ser disparado
    rem_future = await reminder_repo.create_reminder(
        owner_user_id=user.id,
        recipient_phone=user.phone_number,
        message_text="Lembrete: Consulta às 16h",
        send_at=now_dt + timedelta(hours=1),
    )
    await db_session.commit()

    # Mock do WhatsAppClient simulando envio com sucesso (HTTP 200)
    mock_client = AsyncMock(spec=WhatsAppClient)
    mock_client.send_text_message.return_value = True

    # Cria session factory pontual para o banco de teste
    test_session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    dispatcher = ReminderDispatcherService(
        session_factory=test_session_factory,
        whatsapp_client=mock_client,
    )

    count = await dispatcher.dispatch_due_reminders(until_time=now_dt)
    assert count == 1

    # Verifica se o cliente WhatsApp foi chamado corretamente
    mock_client.send_text_message.assert_awaited_once_with(
        recipient_phone="5511999991111",
        text="Lembrete: Reunião às 14h",
    )

    # Valida atualização de status no banco de dados
    async with test_session_factory() as verify_session:
        v_repo = ReminderRepository(verify_session)
        reminders = await v_repo.list_by_user(user.id)
        status_by_id = {r.id: r.status for r in reminders}

        assert status_by_id[rem_due.id] == ReminderStatus.SENT.value
        assert status_by_id[rem_future.id] == ReminderStatus.PENDING.value


@pytest.mark.asyncio
async def test_dispatch_due_reminders_failure_marks_failed(db_session: AsyncSession):
    """Garante que falha no WhatsApp API marca o lembrete como 'failed' sem travar o worker."""
    tz = ZoneInfo("America/Sao_Paulo")
    user_repo = UserRepository(db_session)
    reminder_repo = ReminderRepository(db_session)

    user = await user_repo.get_or_create("5511999992222", "Mariana")
    now_dt = datetime(2026, 10, 23, 14, 0, tzinfo=tz)

    await reminder_repo.create_reminder(
        owner_user_id=user.id,
        recipient_phone=user.phone_number,
        message_text="Lembrete: Dentista às 15h",
        send_at=now_dt - timedelta(minutes=5),
    )
    await db_session.commit()

    # Mock do WhatsAppClient retornando False (ex: erro 400 ou 500 da Meta)
    mock_client = AsyncMock(spec=WhatsAppClient)
    mock_client.send_text_message.return_value = False

    test_session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    dispatcher = ReminderDispatcherService(
        session_factory=test_session_factory,
        whatsapp_client=mock_client,
    )

    count = await dispatcher.dispatch_due_reminders(until_time=now_dt)
    assert count == 0

    async with test_session_factory() as verify_session:
        v_repo = ReminderRepository(verify_session)
        reminders = await v_repo.list_by_user(user.id)
        assert len(reminders) == 1
        assert reminders[0].status == ReminderStatus.FAILED.value


@pytest.mark.asyncio
async def test_dispatch_due_reminders_exception_marks_failed(db_session: AsyncSession):
    """Garante que exceções de rede/transporte ao comunicar com a Meta marcam como 'failed'."""
    tz = ZoneInfo("America/Sao_Paulo")
    user_repo = UserRepository(db_session)
    reminder_repo = ReminderRepository(db_session)

    user = await user_repo.get_or_create("5511999993333", "Roberto")
    now_dt = datetime(2026, 10, 23, 14, 0, tzinfo=tz)

    await reminder_repo.create_reminder(
        owner_user_id=user.id,
        recipient_phone=user.phone_number,
        message_text="Lembrete: Pagar boleto",
        send_at=now_dt - timedelta(minutes=1),
    )
    await db_session.commit()

    # Mock levantando exceção de conexão
    mock_client = AsyncMock(spec=WhatsAppClient)
    mock_client.send_text_message.side_effect = RuntimeError("Falha de conexão com a Meta Cloud API")

    test_session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    dispatcher = ReminderDispatcherService(
        session_factory=test_session_factory,
        whatsapp_client=mock_client,
    )

    count = await dispatcher.dispatch_due_reminders(until_time=now_dt)
    assert count == 0

    async with test_session_factory() as verify_session:
        v_repo = ReminderRepository(verify_session)
        reminders = await v_repo.list_by_user(user.id)
        assert len(reminders) == 1
        assert reminders[0].status == ReminderStatus.FAILED.value


@pytest.mark.asyncio
async def test_dispatch_idempotency_prevents_duplicate_dispatches(db_session: AsyncSession):
    """Garante idempotência estrita: um lembrete já despachado ('sent') NUNCA é reenviado."""
    tz = ZoneInfo("America/Sao_Paulo")
    user_repo = UserRepository(db_session)
    reminder_repo = ReminderRepository(db_session)

    user = await user_repo.get_or_create("5511999994444", "Fernanda")
    now_dt = datetime(2026, 10, 23, 14, 0, tzinfo=tz)

    await reminder_repo.create_reminder(
        owner_user_id=user.id,
        recipient_phone=user.phone_number,
        message_text="Lembrete: Almoço com cliente",
        send_at=now_dt - timedelta(minutes=10),
    )
    await db_session.commit()

    mock_client = AsyncMock(spec=WhatsAppClient)
    mock_client.send_text_message.return_value = True

    test_session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    dispatcher = ReminderDispatcherService(
        session_factory=test_session_factory,
        whatsapp_client=mock_client,
    )

    # Primeiro ciclo de varredura: deve disparar 1 lembrete
    count_1 = await dispatcher.dispatch_due_reminders(until_time=now_dt)
    assert count_1 == 1
    assert mock_client.send_text_message.await_count == 1

    # Segundo ciclo de varredura imediato: deve encontrar 0 lembretes pendentes
    count_2 = await dispatcher.dispatch_due_reminders(until_time=now_dt)
    assert count_2 == 0
    # O cliente não deve ter sido chamado novamente
    assert mock_client.send_text_message.await_count == 1


@pytest.mark.asyncio
async def test_fastapi_lifespan_initializes_scheduler():
    """Valida se o lifespan do FastAPI inicializa e encerra o APScheduler corretamente."""
    from app.main import app, lifespan

    async with lifespan(app):
        scheduler = app.state.scheduler
        assert scheduler is not None
        assert scheduler.running is True
        job = scheduler.get_job("reminder_sweeper_job")
        assert job is not None

    # Ao sair do contexto, o scheduler deve ser finalizado
    assert scheduler.running is False
