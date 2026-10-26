"""Contratos de interface (ports) para desacoplamento de serviços externos."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TravelEstimate(BaseModel):
    """Estimativa de deslocamento e trânsito (para futura integração com Google Maps)."""
    origin_address: str
    destination_address: str
    departure_time: datetime | None = None
    mode: str = "driving"  # driving, transit, bicycling, walking
    duration_minutes: int
    distance_meters: int | None = None
    distance_km: float | None = None
    summary: str | None = None
    provider: str = "google_maps_platform"
    is_approximate: bool = True
    warning: str | None = None

    def model_post_init(self, __context) -> None:
        if self.distance_km is None and self.distance_meters is not None:
            self.distance_km = round(self.distance_meters / 1000.0, 1)
        elif self.distance_meters is None and self.distance_km is not None:
            self.distance_meters = int(self.distance_km * 1000)
        if not self.summary:
            km_str = f" ({self.distance_km} km)" if self.distance_km else ""
            self.summary = f"~{self.duration_minutes} min{km_str}"


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


class CalendarAdapterProtocol:
    """Protocolo de contrato assíncrono para adaptadores de calendário externo."""

    async def create_event(self, event: Any) -> str | None | ExternalCalendarEvent:
        """Cria evento no calendário externo e retorna ID ou ExternalCalendarEvent."""
        ...

    async def update_event(self, event: Any, event_data: dict[str, Any] | None = None) -> bool | ExternalCalendarEvent:
        """Atualiza evento no calendário externo."""
        ...

    async def delete_event(self, external_id: str) -> bool:
        """Remove evento do calendário externo."""
        ...

    async def list_events(self, start: datetime, end: datetime) -> list[dict]:
        """Lista eventos dentro de um intervalo de datas."""
        ...


class CalendarSyncPort(ABC):
    """Porta para sincronização bidirecional de eventos (ex: Google Agenda)."""

    @abstractmethod
    async def create_event(self, event_data: Any) -> ExternalCalendarEvent:
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
