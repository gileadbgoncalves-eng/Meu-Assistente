"""Contratos de interface (ports) para desacoplamento de serviços externos."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TravelEstimate(BaseModel):
    """Estimativa de deslocamento e trânsito (para futura integração com Google Maps)."""
    origin_address: str
    destination_address: str
    departure_time: datetime
    mode: str = "driving"  # driving, transit, bicycling, walking
    duration_minutes: int
    distance_meters: int | None = None
    provider: str = "google_maps_platform"
    is_approximate: bool = True
    warning: str | None = None


class ExternalCalendarEvent(BaseModel):
    """Estrutura para sincronização externa com Google Agenda."""
    external_id: str
    provider: str = "google_calendar"
    summary: str
    description: str | None = None
    start_time: datetime
    end_time: datetime
    location: str | None = None
    html_link: str | None = None


class CalendarSyncPort(ABC):
    """Porta para sincronização bidirecional de eventos (ex: Google Agenda)."""

    @abstractmethod
    async def create_event(self, event_data: dict[str, Any]) -> ExternalCalendarEvent:
        """Cria evento no calendário externo."""

    @abstractmethod
    async def update_event(self, external_id: str, event_data: dict[str, Any]) -> ExternalCalendarEvent:
        """Atualiza evento no calendário externo."""

    @abstractmethod
    async def delete_event(self, external_id: str) -> bool:
        """Remove evento no calendário externo."""


class TravelTimeEstimatorPort(ABC):
    """Porta para cálculo de tempo de rota e trânsito (ex: Google Maps Platform)."""

    @abstractmethod
    async def estimate_travel_time(
        self,
        origin: str,
        destination: str,
        departure_time: datetime,
        mode: str = "driving",
    ) -> TravelEstimate:
        """Calcula o tempo de viagem e distância prevista."""


class NotificationPort(ABC):
    """Porta de envio de mensagens e notificações (WhatsApp, CLI, etc.)."""

    @abstractmethod
    async def send_message(self, recipient_id: str, message: str) -> bool:
        """Envia mensagem ao destinatário especificado."""
