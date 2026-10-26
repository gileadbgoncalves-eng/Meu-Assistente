"""Testes unitários para o Briefing Matinal Executivo e Gestão de Contatos."""

from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.whatsapp import WhatsAppClient
from app.db.repository import ContactRepository, EventRepository, UserRepository
from app.domain.entities import Event, Location
from app.domain.formatters import ExecutiveBriefingFormatter
from app.services.assistant import AssistantService
from app.services.reminder_dispatcher import ReminderDispatcherService
from tests.conftest import MockGeminiInterpreter

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def mock_whatsapp_client() -> AsyncMock:
    client = AsyncMock(spec=WhatsAppClient)
    client.send_text_message.return_value = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "5511999990001", "wa_id": "5511999990001"}],
        "messages": [{"id": "wamid.mock_briefing"}],
    }
    return client


@pytest.mark.asyncio
async def test_daily_briefing_empty_day(
    db_session: AsyncSession,
    mock_whatsapp_client: AsyncMock,
) -> None:
    """Valida o briefing para um dia totalmente sem compromissos."""
    user_repo = UserRepository(db_session)
    user = await user_repo.get_or_create("5511999990001", "Carlos")
    await db_session.commit()

    test_session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    service = ReminderDispatcherService(
        session_factory=test_session_factory,
        whatsapp_client=mock_whatsapp_client,
    )

    target_date = datetime(2026, 10, 26, 8, 0, tzinfo=SAO_PAULO_TZ)
    briefing = await service.generate_daily_briefing(user.id, target_date)

    assert "☀️ *Bom dia" in briefing
    assert "100% livre" in briefing or "não tem compromissos" in briefing


@pytest.mark.asyncio
async def test_daily_briefing_with_events(
    db_session: AsyncSession,
    mock_whatsapp_client: AsyncMock,
) -> None:
    """Valida o briefing executivo contendo múltiplos compromissos, local e deslocamento."""
    user_repo = UserRepository(db_session)
    user = await user_repo.get_or_create("5511999990001", "Carlos")
    event_repo = EventRepository(db_session)

    # Cria eventos no dia 2026-10-26
    dt1_start = datetime(2026, 10, 26, 9, 0, tzinfo=SAO_PAULO_TZ)
    dt1_end = datetime(2026, 10, 26, 10, 0, tzinfo=SAO_PAULO_TZ)
    dt2_start = datetime(2026, 10, 26, 14, 0, tzinfo=SAO_PAULO_TZ)
    dt2_end = datetime(2026, 10, 26, 15, 0, tzinfo=SAO_PAULO_TZ)

    await event_repo.create_event(
        owner_user_id=user.id,
        title="Reunião de Diretoria",
        start_time=dt1_start,
        end_time=dt1_end,
        location=Location(raw_address="Av Paulista 1000"),
        travel_time_minutes=25,
    )

    await event_repo.create_event(
        owner_user_id=user.id,
        title="Alinhamento com Time Técnico",
        start_time=dt2_start,
        end_time=dt2_end,
    )
    await db_session.commit()

    test_session_factory = async_sessionmaker(
        bind=db_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    service = ReminderDispatcherService(
        session_factory=test_session_factory,
        whatsapp_client=mock_whatsapp_client,
    )

    briefing = await service.generate_daily_briefing(user.id, dt1_start)

    assert "☀️ *Briefing Executivo do Dia*" in briefing
    assert "2 compromissos" in briefing
    assert "Reunião de Diretoria" in briefing
    assert "Av Paulista 1000" in briefing
    assert "🚗 ~25 min" in briefing
    assert "Alinhamento com Time Técnico" in briefing


@pytest.mark.asyncio
async def test_assistant_contact_save_and_query_via_message(
    db_session: AsyncSession,
    mock_interpreter: MockGeminiInterpreter,
) -> None:
    """Valida fluxo completo de salvar e buscar contatos via mensagem de texto."""
    assistant = AssistantService(
        session=db_session,
        interpreter=mock_interpreter,
    )
    phone = "5511999990001"

    # 1. Salvar contato
    save_msg = "Salvar contato Dr. Roberto 21999998888"
    save_reply = await assistant.process_message(phone_number=phone, raw_text=save_msg)

    assert "Dr. Roberto" in save_reply
    assert "salvo com sucesso" in save_reply

    # Verifica no banco de dados isolado do usuário
    user_repo = UserRepository(db_session)
    user = await user_repo.get_by_phone(phone)
    assert user is not None

    contact_repo = ContactRepository(db_session)
    contacts = await contact_repo.search_by_name(owner_user_id=user.id, query="Roberto")
    assert len(contacts) == 1
    assert contacts[0].name == "Dr. Roberto"
    assert contacts[0].phone_number == "21999998888"

    # 2. Buscar contato salvo
    query_msg = "Qual o telefone do Dr. Roberto?"
    query_reply = await assistant.process_message(phone_number=phone, raw_text=query_msg)

    assert "Dr. Roberto" in query_reply
    assert "21999998888" in query_reply

    # 3. Buscar contato inexistente
    not_found_msg = "Qual o telefone da Dra. Fernanda?"
    not_found_reply = await assistant.process_message(phone_number=phone, raw_text=not_found_msg)
    assert "Não encontrei nenhum contato" in not_found_reply


@pytest.mark.asyncio
async def test_assistant_resumo_do_dia_via_message(
    db_session: AsyncSession,
    mock_interpreter: MockGeminiInterpreter,
) -> None:
    """Valida solicitação de resumo do dia via comando de texto."""
    assistant = AssistantService(
        session=db_session,
        interpreter=mock_interpreter,
    )
    phone = "5511999990001"
    user_repo = UserRepository(db_session)
    user = await user_repo.get_or_create(phone, "Carlos")

    # Cria evento para hoje
    now = datetime(2026, 10, 26, 11, 0, tzinfo=SAO_PAULO_TZ)
    event_repo = EventRepository(db_session)
    await event_repo.create_event(
        owner_user_id=user.id,
        title="Check-in Matinal",
        start_time=datetime(2026, 10, 26, 11, 30, tzinfo=SAO_PAULO_TZ),
        end_time=datetime(2026, 10, 26, 12, 0, tzinfo=SAO_PAULO_TZ),
    )

    reply = await assistant.process_message(
        phone_number=phone,
        raw_text="Resumo do meu dia",
        anchor_datetime=now,
    )

    assert "Briefing Executivo" in reply or "Bom dia" in reply
    assert "Check-in Matinal" in reply


@pytest.mark.asyncio
async def test_contact_repository_update_and_delete(db_session: AsyncSession):
    """Valida métodos diretos update e delete do ContactRepository."""
    user_repo = UserRepository(db_session)
    user = await user_repo.get_or_create("5511999990001", "Carlos")
    contact_repo = ContactRepository(db_session)

    contact = await contact_repo.create(
        owner_user_id=user.id,
        name="Bruno Engenheiro",
        phone="5511977776666",
        category="Trabalho",
    )
    assert contact.id is not None

    updated = await contact_repo.update(
        contact_id=contact.id,
        owner_user_id=user.id,
        category="Parceiro Comercial",
        phone="5511977778888",
    )
    assert updated is not None
    assert updated.category == "Parceiro Comercial"
    assert updated.phone_number == "5511977778888"

    deleted = await contact_repo.delete(contact_id=contact.id, owner_user_id=user.id)
    assert deleted is True

    missing = await contact_repo.get_by_id(contact.id, owner_user_id=user.id)
    assert missing is None


@pytest.mark.asyncio
async def test_contact_save_without_phone(
    db_session: AsyncSession,
    mock_interpreter: MockGeminiInterpreter,
):
    """Valida salvamento de contato que possui apenas nome, sem telefone."""
    assistant = AssistantService(session=db_session, interpreter=mock_interpreter)
    phone = "5511999990001"

    reply = await assistant.process_message(phone_number=phone, raw_text="Salvar contato Roberta Freitas")
    assert "Roberta Freitas" in reply
    assert "salvo com sucesso" in reply


@pytest.mark.asyncio
async def test_contact_query_case_insensitive_and_partial(
    db_session: AsyncSession,
    mock_interpreter: MockGeminiInterpreter,
):
    """Valida busca parcial de contatos e com variações de maiúsculas/minúsculas."""
    user_repo = UserRepository(db_session)
    user = await user_repo.get_or_create("5511999990001", "Carlos")
    contact_repo = ContactRepository(db_session)
    await contact_repo.create(
        owner_user_id=user.id,
        name="Dra. Juliana Alencar",
        phone="5511966665555",
    )

    assistant = AssistantService(session=db_session, interpreter=mock_interpreter)
    reply = await assistant.process_message(phone_number="5511999990001", raw_text="Qual o telefone da Juliana?")
    assert "Juliana" in reply
    assert "5511966665555" in reply


def test_executive_briefing_formatter_single_event():
    """Valida formatação correta para um único compromisso no dia."""
    dt = datetime(2026, 10, 26, 15, 0, tzinfo=SAO_PAULO_TZ)
    events = [
        Event(
            id=1,
            owner_user_id=1,
            title="Sessão de Mentoria",
            start_time=dt,
            end_time=datetime(2026, 10, 26, 16, 0, tzinfo=SAO_PAULO_TZ),
            location=Location(raw_address="Google Meet"),
        )
    ]
    formatted = ExecutiveBriefingFormatter.format_briefing(
        user_name="Carlos",
        target_date=dt,
        events=events,
    )
    assert "1 compromisso:" in formatted
    assert "Sessão de Mentoria" in formatted
    assert "Google Meet" in formatted

