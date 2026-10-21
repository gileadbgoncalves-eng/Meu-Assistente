"""Entidades de domínio puras (Pydantic models desacoplados de persistência)."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.domain.enums import (
    EventStatus,
    ParticipantRole,
    ParticipantStatus,
    SyncStatus,
    UserStatus,
    ActionType,
)


class Location(BaseModel):
    """Localização física de um evento."""
    id: Optional[int] = None
    raw_address: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    place_id: Optional[str] = None  # ID do Google Places para integração futura
    arrival_instructions: Optional[str] = None


class EventParticipant(BaseModel):
    """Participante de um compromisso."""
    id: Optional[int] = None
    event_id: Optional[int] = None
    name: str
    phone_number: Optional[str] = None
    contact_id: Optional[int] = None
    user_id: Optional[int] = None
    role: ParticipantRole = ParticipantRole.ATTENDEE
    status: ParticipantStatus = ParticipantStatus.PENDING
    replaces_participant_id: Optional[int] = None


class Event(BaseModel):
    """Entidade central de compromisso da agenda."""
    id: Optional[int] = None
    owner_user_id: int
    title: str
    description: Optional[str] = None
    start_time: datetime
    end_time: datetime
    timezone: str = "America/Sao_Paulo"
    location: Optional[Location] = None
    location_id: Optional[int] = None
    status: EventStatus = EventStatus.DRAFT
    confidence_score: float = 1.0
    participants: list[EventParticipant] = Field(default_factory=list)

    # Campos de integração externa (Google Agenda)
    external_calendar_id: Optional[str] = None
    external_provider: Optional[str] = None
    sync_status: SyncStatus = SyncStatus.NOT_SYNCED

    # Campos de estimativa de deslocamento (Google Maps)
    estimated_travel_minutes: Optional[int] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class User(BaseModel):
    """Usuário do sistema / proprietário da agenda."""
    id: Optional[int] = None
    phone_number: str
    display_name: str
    timezone: str = "America/Sao_Paulo"
    status: UserStatus = UserStatus.ACTIVE
    consent_given: bool = True
    created_at: Optional[datetime] = None


class Contact(BaseModel):
    """Contato registrado pelo usuário."""
    id: Optional[int] = None
    owner_user_id: int
    name: str
    phone_number: Optional[str] = None
    external_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


class ChangeLogEntry(BaseModel):
    """Registro imutável na trilha de auditoria."""
    id: Optional[int] = None
    entity_name: str
    entity_id: int
    author_user_id: int
    action: ActionType
    previous_values: Optional[dict] = None
    new_values: Optional[dict] = None
    reason: Optional[str] = None
    created_at: Optional[datetime] = None
