"""Testes automatizados da camada de persistência relacional e repositórios."""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.repository import (
    ChangeLogRepository,
    EventRepository,
    IdempotencyRepository,
    UserRepository,
)
from app.domain.enums import ActionType, EventStatus
from app.domain.exceptions import IdempotencyDuplicateError


@pytest.mark.asyncio
async def test_user_creation_and_retrieval(db_session):
    """Testa criação e busca de usuário pelo telefone."""
    user_repo = UserRepository(db_session)

    user = await user_repo.get_or_create(
        phone_number="5511999998888",
        display_name="Bernardo Silva",
        user_timezone="America/Sao_Paulo",
    )
    assert user.id is not None
    assert user.phone_number == "5511999998888"
    assert user.display_name == "Bernardo Silva"

    # Segunda chamada não duplica usuário
    same_user = await user_repo.get_or_create(
        phone_number="5511999998888",
        display_name="Outro Nome",
    )
    assert same_user.id == user.id
    assert same_user.display_name == "Bernardo Silva"


@pytest.mark.asyncio
async def test_event_creation_with_participants_and_audit(db_session):
    """Testa criação de compromisso, seus participantes e o registro em change_log."""
    user_repo = UserRepository(db_session)
    event_repo = EventRepository(db_session)
    changelog_repo = ChangeLogRepository(db_session)

    user = await user_repo.get_or_create("5511999998888", "Bernardo Silva")

    start = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    end = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)

    event = await event_repo.create_event(
        owner_user_id=user.id,
        title="Reunião de Alinhamento",
        start_time=start,
        end_time=end,
        description="Alinhamento do projeto",
        location_address="Av. Paulista, 1000 - Bela Vista, SP",
        participants_data=[
            {"name": "Caio", "role": "attendee", "status": "pending"},
            {"name": "Pedro", "role": "attendee", "status": "confirmed"},
        ],
        status=EventStatus.CONFIRMED.value,
    )

    assert event.id is not None
    assert event.title == "Reunião de Alinhamento"
    assert len(event.participants) == 2
    assert event.location is not None
    assert "Av. Paulista" in event.location.raw_address

    # Verifica se a trilha de auditoria (change_log) registrou a criação
    history = await changelog_repo.get_history_for_entity("event", event.id)
    assert len(history) == 1
    assert history[0].action == ActionType.CREATE.value
    assert history[0].author_user_id == user.id
    assert history[0].new_values["title"] == "Reunião de Alinhamento"


@pytest.mark.asyncio
async def test_event_conflict_detection(db_session):
    """Testa detecção determinística de sobreposição de horários."""
    user_repo = UserRepository(db_session)
    event_repo = EventRepository(db_session)

    user = await user_repo.get_or_create("5511999998888", "Bernardo Silva")

    # Evento base: 14:00 às 15:00
    base_start = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    base_end = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
    base_event = await event_repo.create_event(
        owner_user_id=user.id,
        title="Consulta Médica",
        start_time=base_start,
        end_time=base_end,
        status=EventStatus.CONFIRMED.value,
    )

    # 1. Conflito: sobreposição parcial antes (13:30 às 14:30)
    conflicts_1 = await event_repo.find_conflicts(
        owner_user_id=user.id,
        start_time=base_start - timedelta(minutes=30),
        end_time=base_start + timedelta(minutes=30),
    )
    assert len(conflicts_1) == 1
    assert conflicts_1[0].id == base_event.id

    # 2. Conflito: sobreposição parcial depois (14:30 às 15:30)
    conflicts_2 = await event_repo.find_conflicts(
        owner_user_id=user.id,
        start_time=base_start + timedelta(minutes=30),
        end_time=base_end + timedelta(minutes=30),
    )
    assert len(conflicts_2) == 1

    # 3. Conflito: evento engloba completamente (13:00 às 16:00)
    conflicts_3 = await event_repo.find_conflicts(
        owner_user_id=user.id,
        start_time=base_start - timedelta(hours=1),
        end_time=base_end + timedelta(hours=1),
    )
    assert len(conflicts_3) == 1

    # 4. NÃO é conflito: evento adjacente imediatamente antes (13:00 às 14:00)
    no_conflict_before = await event_repo.find_conflicts(
        owner_user_id=user.id,
        start_time=base_start - timedelta(hours=1),
        end_time=base_start,
    )
    assert len(no_conflict_before) == 0

    # 5. NÃO é conflito: evento adjacente imediatamente depois (15:00 às 16:00)
    no_conflict_after = await event_repo.find_conflicts(
        owner_user_id=user.id,
        start_time=base_end,
        end_time=base_end + timedelta(hours=1),
    )
    assert len(no_conflict_after) == 0


@pytest.mark.asyncio
async def test_multiuser_security_isolation(db_session):
    """Testa que um usuário não pode visualizar nem modificar compromissos de outro."""
    user_repo = UserRepository(db_session)
    event_repo = EventRepository(db_session)

    user1 = await user_repo.get_or_create("5511999990001", "Usuário A")
    user2 = await user_repo.get_or_create("5511999990002", "Usuário B")

    start = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    end = datetime(2026, 10, 22, 11, 0, tzinfo=UTC)

    # Usuário 1 cria evento
    event_user1 = await event_repo.create_event(
        owner_user_id=user1.id,
        title="Compromisso Confidencial Usuário 1",
        start_time=start,
        end_time=end,
        status=EventStatus.CONFIRMED.value,
    )

    # Usuário 2 tenta buscar pelo ID de evento do Usuário 1 -> Retorna None (isolado)
    accessed_by_user2 = await event_repo.get_by_id(event_user1.id, owner_user_id=user2.id)
    assert accessed_by_user2 is None

    # Usuário 2 lista eventos no mesmo intervalo -> Não vê o evento do Usuário 1
    events_user2 = await event_repo.list_events_in_range(
        owner_user_id=user2.id,
        start_time=start - timedelta(hours=1),
        end_time=end + timedelta(hours=1),
    )
    assert len(events_user2) == 0

    # O Usuário 1 consegue acessar normalmente
    accessed_by_user1 = await event_repo.get_by_id(event_user1.id, owner_user_id=user1.id)
    assert accessed_by_user1 is not None
    assert accessed_by_user1.id == event_user1.id


@pytest.mark.asyncio
async def test_update_and_cancel_audit_trail(db_session):
    """Testa atualização e cancelamento gravando histórico de valores anteriores e novos."""
    user_repo = UserRepository(db_session)
    event_repo = EventRepository(db_session)
    changelog_repo = ChangeLogRepository(db_session)

    user = await user_repo.get_or_create("5511999998888", "Bernardo Silva")
    start = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    end = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)

    event = await event_repo.create_event(
        owner_user_id=user.id,
        title="Reunião Escola",
        start_time=start,
        end_time=end,
        status=EventStatus.DRAFT.value,
    )

    # Atualiza título e status para confirmado
    new_start = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    new_end = datetime(2026, 10, 24, 13, 0, tzinfo=UTC)
    updated = await event_repo.update_event(
        event_id=event.id,
        owner_user_id=user.id,
        updates={
            "title": "Reunião Escola Bernardo",
            "start_time": new_start,
            "end_time": new_end,
            "status": EventStatus.CONFIRMED.value,
        },
        reason="Mudança confirmada pelo usuário após mensagem",
    )
    assert updated.title == "Reunião Escola Bernardo"
    assert updated.status == EventStatus.CONFIRMED.value

    # Cancela o compromisso
    cancelled = await event_repo.cancel_event(
        event_id=event.id,
        owner_user_id=user.id,
        reason="Cancelado a pedido do usuário",
    )
    assert cancelled.status == EventStatus.CANCELLED.value

    # Verifica os 3 registros de auditoria em change_log
    history = await changelog_repo.get_history_for_entity("event", event.id)
    assert len(history) == 3
    assert history[0].action == ActionType.CREATE.value
    assert history[1].action == ActionType.UPDATE.value
    assert history[1].previous_values["title"] == "Reunião Escola"
    assert history[1].new_values["title"] == "Reunião Escola Bernardo"
    assert history[2].action == ActionType.UPDATE.value
    assert history[2].new_values["status"] == EventStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_idempotency_prevents_duplicate_processing(db_session):
    """Testa que a mesma chave de mensagem/webhook é rejeitada na repetição."""
    user_repo = UserRepository(db_session)
    idempotency_repo = IdempotencyRepository(db_session)

    user = await user_repo.get_or_create("5511999998888", "Bernardo Silva")

    # Primeira recepção do webhook
    msg_record = await idempotency_repo.register_message(
        idempotency_key="wamid.HBgLNTUxMTk5...",
        user_id=user.id,
        raw_text="reunião amanhã às 15h",
    )
    assert msg_record.id is not None
    assert msg_record.status == "received"

    # Segunda recepção da mesma mensagem deve lançar IdempotencyDuplicateError
    with pytest.raises(IdempotencyDuplicateError):
        await idempotency_repo.register_message(
            idempotency_key="wamid.HBgLNTUxMTk5...",
            user_id=user.id,
            raw_text="reunião amanhã às 15h",
        )
