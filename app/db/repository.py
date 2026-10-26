"""Camada de repositório assíncrono com SQLAlchemy 2.0 e isolamento de segurança."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    ChangeLogModel,
    ContactModel,
    EventModel,
    EventParticipantModel,
    LocationModel,
    MessageProcessingModel,
    ReminderModel,
    UserModel,
)
from app.domain.enums import ActionType, EventStatus, IdempotencyStatus, ReminderStatus
from app.domain.exceptions import (
    EntityNotFoundError,
    IdempotencyDuplicateError,
)


class UserRepository:
    """Repositório de acesso e gestão de usuários."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: int) -> UserModel | None:
        result = await self.session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone_number: str) -> UserModel | None:
        result = await self.session.execute(
            select(UserModel).where(UserModel.phone_number == phone_number)
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        phone_number: str,
        display_name: str,
        user_timezone: str = "America/Sao_Paulo",
    ) -> UserModel:
        user = await self.get_by_phone(phone_number)
        if not user:
            user = UserModel(
                phone_number=phone_number,
                display_name=display_name,
                timezone=user_timezone,
            )
            self.session.add(user)
            await self.session.flush()
        return user


class EventRepository:
    """Repositório de eventos com escopo estrito por usuário e auditoria append-only."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_event(
        self,
        owner_user_id: int,
        title: str,
        start_time: datetime,
        end_time: datetime,
        timezone: str = "America/Sao_Paulo",
        description: str | None = None,
        location_address: str | None = None,
        participants_data: list[dict] | None = None,
        status: str = EventStatus.DRAFT.value,
        confidence_score: float = 1.0,
        author_user_id: int | None = None,
        location: str | None = None,
        external_calendar_id: str | None = None,
        travel_time_minutes: int | None = None,
        contact_id: int | None = None,
    ) -> EventModel:
        author_id = author_user_id or owner_user_id

        # Se houver localização, cria registro e armazena texto
        effective_location = location or location_address
        loc_str = getattr(effective_location, "raw_address", effective_location) if effective_location else None
        location_id = None
        if loc_str:
            loc_record = LocationModel(raw_address=str(loc_str))
            self.session.add(loc_record)
            await self.session.flush()
            location_id = loc_record.id

        event = EventModel(
            owner_user_id=owner_user_id,
            title=title,
            description=description,
            start_time=start_time,
            end_time=end_time,
            timezone=timezone,
            location_id=location_id,
            location_name=str(loc_str) if loc_str else None,
            contact_id=contact_id,
            external_calendar_id=external_calendar_id,
            travel_time_minutes=travel_time_minutes,
            estimated_travel_minutes=travel_time_minutes,
            status=status,
            confidence_score=confidence_score,
        )
        self.session.add(event)
        await self.session.flush()

        # Adiciona participantes
        if participants_data:
            for p in participants_data:
                participant = EventParticipantModel(
                    event_id=event.id,
                    name=p.get("name", "Participante"),
                    phone_number=p.get("phone_number"),
                    contact_id=p.get("contact_id"),
                    role=p.get("role", "attendee"),
                    status=p.get("status", "pending"),
                    replaces_participant_id=p.get("replaces_participant_id"),
                )
                self.session.add(participant)
            await self.session.flush()

        # Grava trilha de auditoria append-only
        audit_log = ChangeLogModel(
            entity_name="event",
            entity_id=event.id,
            author_user_id=author_id,
            action=ActionType.CREATE.value,
            new_values={
                "title": title,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "status": status,
            },
            reason="Criação de compromisso",
        )
        self.session.add(audit_log)
        await self.session.flush()

        # Recarrega o evento com os participantes
        return await self.get_by_id(event.id, owner_user_id=owner_user_id)  # type: ignore

    async def get_by_id(self, event_id: int, owner_user_id: int) -> EventModel | None:
        """Busca evento garantindo isolamento: só retorna se pertencer ao owner_user_id."""
        query = (
            select(EventModel)
            .options(
                selectinload(EventModel.participants),
                selectinload(EventModel.location),
            )
            .where(
                and_(
                    EventModel.id == event_id,
                    EventModel.owner_user_id == owner_user_id,
                )
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def find_conflicts(
        self,
        owner_user_id: int,
        start_time: datetime,
        end_time: datetime,
        exclude_event_id: int | None = None,
    ) -> Sequence[EventModel]:
        """Busca eventos confirmados ou rascunhos que se sobreponham ao intervalo informado."""
        filters = [
            EventModel.owner_user_id == owner_user_id,
            EventModel.status.in_([EventStatus.CONFIRMED.value, EventStatus.DRAFT.value]),
            # Condição de sobreposição: start_A < end_B AND end_A > start_B
            EventModel.start_time < end_time,
            EventModel.end_time > start_time,
        ]
        if exclude_event_id is not None:
            filters.append(EventModel.id != exclude_event_id)

        query = (
            select(EventModel)
            .options(selectinload(EventModel.participants))
            .where(and_(*filters))
            .order_by(EventModel.start_time.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def list_events_in_range(
        self,
        owner_user_id: int,
        start_time: datetime,
        end_time: datetime,
        include_cancelled: bool = False,
    ) -> Sequence[EventModel]:
        """Lista compromissos do usuário dentro de um período."""
        statuses = [EventStatus.CONFIRMED.value, EventStatus.DRAFT.value]
        if include_cancelled:
            statuses.append(EventStatus.CANCELLED.value)

        query = (
            select(EventModel)
            .options(
                selectinload(EventModel.participants),
                selectinload(EventModel.location),
            )
            .where(
                and_(
                    EventModel.owner_user_id == owner_user_id,
                    EventModel.status.in_(statuses),
                    EventModel.start_time >= start_time,
                    EventModel.start_time <= end_time,
                )
            )
            .order_by(EventModel.start_time.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def update_event(
        self,
        event_id: int,
        owner_user_id: int,
        updates: dict,
        author_user_id: int | None = None,
        reason: str | None = None,
    ) -> EventModel:
        """Atualiza campos de um evento e registra a mudança na trilha de auditoria."""
        event = await self.get_by_id(event_id, owner_user_id=owner_user_id)
        if not event:
            raise EntityNotFoundError("Evento", event_id)

        previous_values = {}
        for key, value in updates.items():
            if hasattr(event, key):
                current_val = getattr(event, key)
                previous_values[key] = (
                    current_val.isoformat() if isinstance(current_val, datetime) else current_val
                )
                setattr(event, key, value)

        # Grava trilha de auditoria
        audit = ChangeLogModel(
            entity_name="event",
            entity_id=event.id,
            author_user_id=author_user_id or owner_user_id,
            action=ActionType.UPDATE.value,
            previous_values=previous_values,
            new_values={
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in updates.items()
            },
            reason=reason or "Atualização de compromisso",
        )
        self.session.add(audit)
        await self.session.flush()
        return event

    async def cancel_event(
        self,
        event_id: int,
        owner_user_id: int,
        author_user_id: int | None = None,
        reason: str | None = None,
    ) -> EventModel:
        """Cancela um evento e registra o motivo no change_log."""
        return await self.update_event(
            event_id=event_id,
            owner_user_id=owner_user_id,
            updates={"status": EventStatus.CANCELLED.value},
            author_user_id=author_user_id,
            reason=reason or "Cancelamento de compromisso",
        )


class IdempotencyRepository:
    """Gerencia deduplicação de mensagens e webhooks para evitar operações repetidas."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def register_message(
        self,
        idempotency_key: str,
        user_id: int,
        raw_text: str,
    ) -> MessageProcessingModel:
        """Registra a mensagem; se a chave já existir, lança IdempotencyDuplicateError."""
        query = select(MessageProcessingModel).where(
            MessageProcessingModel.idempotency_key == idempotency_key
        )
        result = await self.session.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            raise IdempotencyDuplicateError(idempotency_key)

        record = MessageProcessingModel(
            idempotency_key=idempotency_key,
            user_id=user_id,
            raw_text=raw_text,
            status=IdempotencyStatus.RECEIVED.value,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def update_status(
        self,
        idempotency_key: str,
        status: str | IdempotencyStatus,
        detected_intent: str | None = None,
    ) -> None:
        query = select(MessageProcessingModel).where(
            MessageProcessingModel.idempotency_key == idempotency_key
        )
        result = await self.session.execute(query)
        record = result.scalar_one_or_none()
        if record:
            record.status = status.value if hasattr(status, "value") else str(status)
            if detected_intent:
                record.detected_intent = detected_intent
            await self.session.flush()


class ChangeLogRepository:
    """Consulta à trilha de auditoria append-only."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_history_for_entity(
        self, entity_name: str, entity_id: int
    ) -> Sequence[ChangeLogModel]:
        query = (
            select(ChangeLogModel)
            .where(
                and_(
                    ChangeLogModel.entity_name == entity_name,
                    ChangeLogModel.entity_id == entity_id,
                )
            )
            .order_by(ChangeLogModel.created_at.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()


class ReminderRepository:
    """Repositório de persistência e consulta de lembretes."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_reminder(
        self,
        owner_user_id: int,
        recipient_phone: str,
        message_text: str,
        send_at: datetime,
        event_id: int | None = None,
        channel: str = "whatsapp",
        status: str = "pending",
        consent_given: bool = True,
    ) -> ReminderModel:
        """Cria e persiste um lembrete (avulso ou vinculado a evento)."""
        reminder = ReminderModel(
            owner_user_id=owner_user_id,
            event_id=event_id,
            recipient_phone=recipient_phone,
            message_text=message_text,
            send_at=send_at,
            channel=channel,
            status=status,
            consent_given=consent_given,
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def list_by_user(
        self, owner_user_id: int, status: str | None = None
    ) -> Sequence[ReminderModel]:
        """Lista lembretes de um usuário específico."""
        conditions = [ReminderModel.owner_user_id == owner_user_id]
        if status:
            conditions.append(ReminderModel.status == status)

        query = (
            select(ReminderModel)
            .where(and_(*conditions))
            .order_by(ReminderModel.send_at.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def list_by_event(self, event_id: int) -> Sequence[ReminderModel]:
        """Lista lembretes vinculados a um evento."""
        query = (
            select(ReminderModel)
            .where(ReminderModel.event_id == event_id)
            .order_by(ReminderModel.send_at.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def list_pending_due_reminders(
        self, until_time: datetime
    ) -> Sequence[ReminderModel]:
        """Busca todos os lembretes pendentes cujo horário de disparo é menor ou igual ao informado."""
        query = (
            select(ReminderModel)
            .where(
                and_(
                    ReminderModel.status == ReminderStatus.PENDING.value,
                    ReminderModel.send_at <= until_time,
                )
            )
            .order_by(ReminderModel.send_at.asc())
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def update_status(
        self, reminder_id: int, status: str | ReminderStatus
    ) -> None:
        """Atualiza o status de um lembrete (ex: sent, failed)."""
        status_val = status.value if hasattr(status, "value") else str(status)
        query = select(ReminderModel).where(ReminderModel.id == reminder_id)
        result = await self.session.execute(query)
        reminder = result.scalar_one_or_none()
        if reminder:
            reminder.status = status_val
            await self.session.flush()


class ContactRepository:
    """Repositório de contatos com isolamento estrito por usuário."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        owner_user_id: int,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        category: str | None = None,
        notes: str | None = None,
        user_id: int | None = None,
    ) -> ContactModel:
        actual_owner_id = user_id if user_id is not None else owner_user_id
        contact = ContactModel(
            owner_user_id=actual_owner_id,
            name=name,
            phone_number=phone,
            email=email,
            category=category,
            notes=notes,
        )
        self.session.add(contact)
        await self.session.flush()
        return contact

    async def get_by_id(self, contact_id: int, owner_user_id: int) -> ContactModel | None:
        result = await self.session.execute(
            select(ContactModel).where(
                and_(
                    ContactModel.id == contact_id,
                    ContactModel.owner_user_id == owner_user_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def search_by_name(self, owner_user_id: int, query: str) -> list[ContactModel]:
        stmt = (
            select(ContactModel)
            .where(
                and_(
                    ContactModel.owner_user_id == owner_user_id,
                    ContactModel.name.ilike(f"%{query}%"),
                )
            )
            .order_by(ContactModel.name.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_user(
        self, owner_user_id: int, limit: int = 50, offset: int = 0
    ) -> list[ContactModel]:
        stmt = (
            select(ContactModel)
            .where(ContactModel.owner_user_id == owner_user_id)
            .order_by(ContactModel.name.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self, contact_id: int, owner_user_id: int, **kwargs
    ) -> ContactModel | None:
        contact = await self.get_by_id(contact_id, owner_user_id=owner_user_id)
        if not contact:
            return None
        if "phone" in kwargs and "phone_number" not in kwargs:
            kwargs["phone_number"] = kwargs.pop("phone")
        for key, value in kwargs.items():
            if hasattr(contact, key):
                setattr(contact, key, value)
        await self.session.flush()
        return contact

    async def delete(self, contact_id: int, owner_user_id: int) -> bool:
        contact = await self.get_by_id(contact_id, owner_user_id=owner_user_id)
        if not contact:
            return False
        await self.session.delete(contact)
        await self.session.flush()
        return True


