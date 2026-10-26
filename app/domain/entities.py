"""Entidades de domínio puras (Pydantic models desacoplados de persistência)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.enums import (
    ActionType,
    EventStatus,
    ParticipantRole,
    ParticipantStatus,
    SyncStatus,
    UserStatus,
)


class Location(BaseModel):
    """Localização física de um evento."""
    id: int | None = None
    raw_address: str
    latitude: float | None = None
    longitude: float | None = None
    place_id: str | None = None  # ID do Google Places para integração futura
    arrival_instructions: str | None = None


class EventParticipant(BaseModel):
    """Participante de um compromisso."""
    id: int | None = None
    event_id: int | None = None
    name: str
    phone_number: str | None = None
    contact_id: int | None = None
    user_id: int | None = None
    role: ParticipantRole = ParticipantRole.ATTENDEE
    status: ParticipantStatus = ParticipantStatus.PENDING
    replaces_participant_id: int | None = None


class Event(BaseModel):
    """Entidade central de compromisso da agenda."""
    id: int | None = None
    owner_user_id: int
    title: str
    description: str | None = None
    start_time: datetime
    end_time: datetime
    timezone: str = "America/Sao_Paulo"
    location: Location | str | None = None
    location_id: int | None = None
    contact_id: int | None = None
    status: EventStatus = EventStatus.DRAFT
    confidence_score: float = 1.0
    participants: list[EventParticipant] = Field(default_factory=list)

    # Campos de integração externa (Google Agenda)
    external_calendar_id: str | None = None
    external_provider: str | None = None
    sync_status: SyncStatus = SyncStatus.NOT_SYNCED

    # Campos de estimativa de deslocamento (Google Maps)
    travel_time_minutes: int | None = None
    estimated_travel_minutes: int | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    def model_post_init(self, __context) -> None:
        if self.travel_time_minutes is not None and self.estimated_travel_minutes is None:
            self.estimated_travel_minutes = self.travel_time_minutes
        elif self.estimated_travel_minutes is not None and self.travel_time_minutes is None:
            self.travel_time_minutes = self.estimated_travel_minutes


class User(BaseModel):
    """Usuário do sistema / proprietário da agenda."""
    id: int | None = None
    phone_number: str
    display_name: str
    timezone: str = "America/Sao_Paulo"
    status: UserStatus = UserStatus.ACTIVE
    consent_given: bool = True
    created_at: datetime | None = None


class Contact(BaseModel):
    """Contato registrado pelo usuário."""
    id: int | None = None
    user_id: int | None = None
    owner_user_id: int | None = None
    name: str
    phone: str | None = None
    phone_number: str | None = None
    email: str | None = None
    category: str | None = None
    external_id: str | None = None
    notes: str | None = None
    created_at: datetime | None = None

    def model_post_init(self, __context) -> None:
        if self.user_id is None and self.owner_user_id is not None:
            self.user_id = self.owner_user_id
        elif self.owner_user_id is None and self.user_id is not None:
            self.owner_user_id = self.user_id
        if self.phone is None and self.phone_number is not None:
            self.phone = self.phone_number
        elif self.phone_number is None and self.phone is not None:
            self.phone_number = self.phone


class ChangeLogEntry(BaseModel):
    """Registro imutável na trilha de auditoria."""
    id: int | None = None
    entity_name: str
    entity_id: int
    author_user_id: int
    action: ActionType
    previous_values: dict | None = None
    new_values: dict | None = None
    reason: str | None = None
    created_at: datetime | None = None
