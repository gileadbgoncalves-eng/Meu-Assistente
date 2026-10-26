"""Schemas Pydantic para a API REST v1 e Dashboard Web."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EventCreateRequest(BaseModel):
    title: str = Field(..., description="Título ou descrição do compromisso")
    start_time: datetime = Field(..., description="Data e hora de início")
    end_time: datetime = Field(..., description="Data e hora de término")
    location: str | None = Field(None, description="Endereço ou nome do local")
    description: str | None = Field(None, description="Notas ou observações adicionais")
    travel_time_minutes: int | None = Field(None, description="Tempo estimado de deslocamento em minutos")
    reminder_minutes_before: int = Field(60, description="Minutos de antecedência para o lembrete")
    contact_id: int | None = Field(None, description="ID do contato associado")
    main_contact_id: int | None = Field(None, description="ID do contato principal associado")
    participant_ids: list[int] = Field(default_factory=list, description="IDs dos contatos participantes")


class EventUpdateRequest(BaseModel):
    title: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str | None = None
    description: str | None = None
    travel_time_minutes: int | None = None
    status: str | None = None
    contact_id: int | None = None
    main_contact_id: int | None = None
    participant_ids: list[int] | None = None


class EventResponse(BaseModel):
    id: int
    title: str
    description: str | None = None
    start_time: datetime
    end_time: datetime
    timezone: str
    status: str
    location: str | None = None
    travel_time_minutes: int | None = None
    external_calendar_id: str | None = None
    contact_id: int | None = None
    main_contact_id: int | None = None
    participant_ids: list[int] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ContactCreateRequest(BaseModel):
    name: str = Field(..., description="Nome do contato")
    phone: str | None = Field(None, description="Número de telefone / WhatsApp")
    email: str | None = Field(None, description="E-mail")
    category: str | None = Field(None, description="Categoria (Pessoal, Trabalho, etc.)")
    notes: str | None = Field(None, description="Observações")


class ContactUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    category: str | None = None
    notes: str | None = None


class ContactResponse(BaseModel):
    id: int
    name: str
    phone: str | None = None
    email: str | None = None
    category: str | None = None
    notes: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ReminderResponse(BaseModel):
    id: int
    event_id: int | None = None
    recipient_phone: str
    message_text: str
    send_at: datetime
    status: str
    attempts_count: int

    model_config = ConfigDict(from_attributes=True)


class BriefingResponse(BaseModel):
    user_name: str
    target_date: str
    events_count: int
    briefing_text: str


class SystemStatusResponse(BaseModel):
    status: str
    version: str
    user_phone: str
    google_calendar_status: str
    google_maps_status: str
    whatsapp_status: str
