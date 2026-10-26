"""Modelos de banco de dados relacionais mapeados com SQLAlchemy 2.0."""

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.domain.enums import (
    EventStatus,
    ParticipantRole,
    ParticipantStatus,
    SyncStatus,
    UserStatus,
)


def utc_now() -> datetime:
    """Retorna datetime UTC atual ciente de fuso horário."""
    return datetime.now(UTC)


class UserModel(Base):
    """Tabela de usuários cadastrados no sistema."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=UserStatus.ACTIVE.value, nullable=False)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_reminder_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relacionamentos
    events: Mapped[list["EventModel"]] = relationship("EventModel", back_populates="owner", cascade="all, delete-orphan")
    contacts: Mapped[list["ContactModel"]] = relationship("ContactModel", back_populates="owner", cascade="all, delete-orphan")
    reminders: Mapped[list["ReminderModel"]] = relationship("ReminderModel", back_populates="owner", cascade="all, delete-orphan")



class ContactModel(Base):
    """Tabela de contatos conhecidos do usuário."""
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    owner: Mapped["UserModel"] = relationship("UserModel", back_populates="contacts")

    @property
    def user_id(self) -> int:
        return self.owner_user_id

    @property
    def phone(self) -> str | None:
        return self.phone_number

    __table_args__ = (
        Index("ix_contacts_owner_name", "owner_user_id", "name"),
    )


class LocationModel(Base):
    """Tabela de locais georreferenciados para compromissos."""
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_address: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    place_id: Mapped[str | None] = mapped_column(String(256), nullable=True)  # Google Maps Place ID
    arrival_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class EventModel(Base):
    """Tabela central de eventos da agenda."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo", nullable=False)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"), nullable=True)
    location_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=EventStatus.DRAFT.value, index=True, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # Campos de integração externa (Google Agenda)
    external_calendar_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    external_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(32), default=SyncStatus.NOT_SYNCED.value, nullable=False)

    # Campos de integração futura com trânsito e rotas (Google Maps)
    travel_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_travel_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relacionamentos
    owner: Mapped["UserModel"] = relationship("UserModel", back_populates="events")
    location: Mapped[Optional["LocationModel"]] = relationship("LocationModel")
    participants: Mapped[list["EventParticipantModel"]] = relationship(
        "EventParticipantModel",
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    reminders: Mapped[list["ReminderModel"]] = relationship(
        "ReminderModel",
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        # Índice composto para consultas rápidas de conflito de agenda por usuário
        Index("ix_events_owner_interval", "owner_user_id", "start_time", "end_time"),
    )


class EventParticipantModel(Base):
    """Participantes e convidados de um evento."""
    __tablename__ = "event_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default=ParticipantRole.ATTENDEE.value, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=ParticipantStatus.PENDING.value, nullable=False)
    replaces_participant_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_participants.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    event: Mapped["EventModel"] = relationship("EventModel", back_populates="participants")
    contact: Mapped[Optional["ContactModel"]] = relationship("ContactModel")


class AvailabilityRuleModel(Base):
    """Regras de disponibilidade e rotina fixa do usuário (trabalho, plantões)."""
    __tablename__ = "availability_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)  # working_hours, blocked, on_call
    days_of_week: Mapped[str] = mapped_column(String(32), nullable=False)  # ex: "0,1,2,3,4" (seg-sex)
    start_time: Mapped[str] = mapped_column(String(8), nullable=False)    # ex: "08:00"
    end_time: Mapped[str] = mapped_column(String(8), nullable=False)      # ex: "18:00"
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo", nullable=False)


class ReminderModel(Base):
    """Lembretes agendados para eventos ou avulsos."""
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True, nullable=True)
    recipient_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), default="whatsapp", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    owner: Mapped["UserModel"] = relationship("UserModel", back_populates="reminders")
    event: Mapped[Optional["EventModel"]] = relationship("EventModel", back_populates="reminders")



class ChangeLogModel(Base):
    """Trilha de auditoria append-only para todas as operações sensíveis."""
    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True, nullable=False)


class MessageProcessingModel(Base):
    """Registro de idempotência para garantir que webhooks/mensagens repetidas não duplicam ações."""
    __tablename__ = "message_processing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    detected_intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="received", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


# Aliases para compatibilidade total com nomenclatura ORM
UserModelOrm = UserModel
UserOrm = UserModel
EventModelOrm = EventModel
EventOrm = EventModel
ContactModelOrm = ContactModel
ContactOrm = ContactModel
ReminderModelOrm = ReminderModel
ReminderOrm = ReminderModel
LocationModelOrm = LocationModel
LocationOrm = LocationModel
EventParticipantModelOrm = EventParticipantModel
EventParticipantOrm = EventParticipantModel
ChangeLogModelOrm = ChangeLogModel
ChangeLogOrm = ChangeLogModel
MessageProcessingModelOrm = MessageProcessingModel
MessageProcessingOrm = MessageProcessingModel
