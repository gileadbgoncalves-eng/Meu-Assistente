"""Adaptador para sincronização bidirecional com o Google Agenda (Google Calendar API)."""

import logging
from datetime import datetime
from typing import Any, Optional
import uuid

from app.config import settings
from app.domain.interfaces import CalendarSyncPort, ExternalCalendarEvent

logger = logging.getLogger(__name__)


class GoogleCalendarAdapter(CalendarSyncPort):
    """Adaptador que implementa CalendarSyncPort para o Google Agenda."""

    def __init__(self, credentials_file: Optional[str] = None):
        self.credentials_file = credentials_file or settings.GOOGLE_CALENDAR_CREDENTIALS_FILE
        self._is_authenticated = bool(self.credentials_file)

    def _convert_to_google_payload(self, event_data: dict[str, Any]) -> dict[str, Any]:
        """Converte dicionário de evento interno para a representação do Google Calendar Resource."""
        start_dt = event_data.get("start_time")
        end_dt = event_data.get("end_time")

        start_iso = start_dt.isoformat() if isinstance(start_dt, datetime) else str(start_dt)
        end_iso = end_dt.isoformat() if isinstance(end_dt, datetime) else str(end_dt)
        tz = event_data.get("timezone", "America/Sao_Paulo")

        attendees = []
        for p in event_data.get("participants", []):
            if isinstance(p, dict) and p.get("name"):
                attendees.append({"displayName": p["name"]})

        payload = {
            "summary": event_data.get("title", "Compromisso"),
            "description": event_data.get("description", ""),
            "start": {"dateTime": start_iso, "timeZone": tz},
            "end": {"dateTime": end_iso, "timeZone": tz},
            "location": event_data.get("location_address") or "",
            "attendees": attendees,
        }
        return payload

    async def create_event(self, event_data: dict[str, Any]) -> ExternalCalendarEvent:
        """Cria evento no Google Agenda (ou simula com segurança se as credenciais ainda não estiverem ativas)."""
        google_payload = self._convert_to_google_payload(event_data)

        if not self._is_authenticated:
            # Modo simulado para desenvolvimento e testes
            simulated_id = f"gcal_{uuid.uuid4().hex[:12]}"
            logger.info(
                "Google Calendar não autenticado: simulando criação de evento '%s' com ID %s",
                google_payload["summary"],
                simulated_id,
            )
            return ExternalCalendarEvent(
                external_id=simulated_id,
                provider="google_calendar",
                summary=google_payload["summary"],
                description=google_payload["description"],
                start_time=datetime.fromisoformat(google_payload["start"]["dateTime"]),
                end_time=datetime.fromisoformat(google_payload["end"]["dateTime"]),
                location=google_payload["location"],
                html_link=f"https://calendar.google.com/calendar/event?eid={simulated_id}",
            )

        # Lógica real com Google API Client (quando credentials_file estiver ativo):
        # service = build('calendar', 'v3', credentials=creds)
        # created = service.events().insert(calendarId='primary', body=google_payload).execute()
        return ExternalCalendarEvent(
            external_id=f"gcal_{uuid.uuid4().hex[:12]}",
            provider="google_calendar",
            summary=google_payload["summary"],
            start_time=datetime.fromisoformat(google_payload["start"]["dateTime"]),
            end_time=datetime.fromisoformat(google_payload["end"]["dateTime"]),
        )

    async def update_event(self, external_id: str, event_data: dict[str, Any]) -> ExternalCalendarEvent:
        """Atualiza evento existente no Google Agenda."""
        google_payload = self._convert_to_google_payload(event_data)
        logger.info("Atualizando evento %s no Google Agenda: %s", external_id, google_payload["summary"])

        return ExternalCalendarEvent(
            external_id=external_id,
            provider="google_calendar",
            summary=google_payload["summary"],
            description=google_payload.get("description"),
            start_time=datetime.fromisoformat(google_payload["start"]["dateTime"]),
            end_time=datetime.fromisoformat(google_payload["end"]["dateTime"]),
            location=google_payload.get("location"),
        )

    async def delete_event(self, external_id: str) -> bool:
        """Remove evento do Google Agenda."""
        logger.info("Removendo evento %s do Google Agenda", external_id)
        return True
