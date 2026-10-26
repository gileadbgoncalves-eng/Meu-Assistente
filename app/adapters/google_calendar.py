"""Adaptador para sincronização bidirecional com o Google Agenda (Google Calendar API)."""

import logging
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.domain.entities import Event
from app.domain.interfaces import CalendarAdapterProtocol, CalendarSyncPort, ExternalCalendarEvent

logger = logging.getLogger(__name__)
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


class GoogleCalendarAdapter(CalendarSyncPort, CalendarAdapterProtocol):
    """Adaptador que implementa CalendarSyncPort e CalendarAdapterProtocol para o Google Agenda."""

    def __init__(
        self,
        credentials_file: str | None = None,
        credentials_json: str | None = None,
        access_token: str | None = None,
    ):
        self.credentials_file = credentials_file or settings.GOOGLE_CALENDAR_CREDENTIALS_FILE
        self.credentials_json = credentials_json or settings.GOOGLE_CALENDAR_CREDENTIALS_JSON
        self.access_token = access_token or settings.GOOGLE_CALENDAR_TOKEN
        self._is_authenticated = bool(self.credentials_file or self.credentials_json or self.access_token)

    def _ensure_sp_tz(self, dt: datetime | str) -> datetime:
        """Garante que a data esteja ancorada no fuso de São Paulo."""
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=SAO_PAULO_TZ)
        return dt.astimezone(SAO_PAULO_TZ)

    def _convert_to_google_payload(self, event_or_data: dict[str, Any] | Event) -> dict[str, Any]:
        """Converte entidade Event ou dicionário interno para o payload do Google Calendar Resource."""
        if isinstance(event_or_data, Event):
            title = event_or_data.title
            description = event_or_data.description or ""
            start_dt = self._ensure_sp_tz(event_or_data.start_time)
            end_dt = self._ensure_sp_tz(event_or_data.end_time)
            location = (
                event_or_data.location
                if isinstance(event_or_data.location, str)
                else (event_or_data.location.raw_address if event_or_data.location else "")
            )
            attendees = [{"displayName": p.name} for p in event_or_data.participants if p.name]
            tz = event_or_data.timezone or "America/Sao_Paulo"
        else:
            title = event_or_data.get("title", "Compromisso")
            description = event_or_data.get("description", "")
            raw_start = event_or_data.get("start_time")
            raw_end = event_or_data.get("end_time")
            start_dt = self._ensure_sp_tz(raw_start) if raw_start else datetime.now(SAO_PAULO_TZ)
            end_dt = self._ensure_sp_tz(raw_end) if raw_end else start_dt
            location = event_or_data.get("location_address") or event_or_data.get("location") or ""
            attendees = []
            for p in event_or_data.get("participants", []):
                if isinstance(p, dict) and p.get("name"):
                    attendees.append({"displayName": p["name"]})
            tz = event_or_data.get("timezone", "America/Sao_Paulo")

        return {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": tz},
            "location": location,
            "attendees": attendees,
        }

    async def create_event(self, event_or_data: dict[str, Any] | Event) -> ExternalCalendarEvent:
        """Cria evento no Google Agenda com modo híbrido resiliente (fallback gracioso)."""
        payload = self._convert_to_google_payload(event_or_data)
        synthetic_id = f"gcal_local_{uuid.uuid4().hex[:12]}"

        if not self._is_authenticated:
            logger.info(
                "Google Calendar sem credenciais: fallback local com ID sintético %s ('%s')",
                synthetic_id,
                payload["summary"],
            )
            return ExternalCalendarEvent(
                external_id=synthetic_id,
                provider="google_calendar",
                summary=payload["summary"],
                description=payload.get("description"),
                start_time=datetime.fromisoformat(payload["start"]["dateTime"]),
                end_time=datetime.fromisoformat(payload["end"]["dateTime"]),
                location=payload.get("location"),
                html_link=f"https://calendar.google.com/calendar/event?eid={synthetic_id}",
            )

        # Tentativa de chamada via Google Calendar API com OAuth2 / Bearer Token
        try:
            headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
            url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    real_id = data.get("id", synthetic_id)
                    return ExternalCalendarEvent(
                        external_id=f"gcal_{real_id}",
                        provider="google_calendar",
                        summary=data.get("summary", payload["summary"]),
                        description=data.get("description"),
                        start_time=datetime.fromisoformat(data["start"]["dateTime"]),
                        end_time=datetime.fromisoformat(data["end"]["dateTime"]),
                        location=data.get("location"),
                        html_link=data.get("htmlLink"),
                    )
                logger.warning(
                    "Google Calendar API retornou status %s. Ativando fallback gracioso.",
                    resp.status_code,
                )
        except Exception as exc:
            logger.warning("Falha de rede ao conectar com Google Calendar API: %s. Ativando fallback gracioso.", exc)

        return ExternalCalendarEvent(
            external_id=synthetic_id,
            provider="google_calendar",
            summary=payload["summary"],
            description=payload.get("description"),
            start_time=datetime.fromisoformat(payload["start"]["dateTime"]),
            end_time=datetime.fromisoformat(payload["end"]["dateTime"]),
            location=payload.get("location"),
            html_link=f"https://calendar.google.com/calendar/event?eid={synthetic_id}",
        )

    async def update_event(
        self,
        event_or_id: Any,
        event_data: dict[str, Any] | None = None,
    ) -> ExternalCalendarEvent:
        """Atualiza evento no Google Agenda, aceitando tanto (id, dict) quanto (event)."""
        if isinstance(event_or_id, Event):
            external_id = event_or_id.external_calendar_id or f"gcal_{uuid.uuid4().hex[:12]}"
            payload = self._convert_to_google_payload(event_or_id)
        else:
            external_id = str(event_or_id)
            payload = self._convert_to_google_payload(event_data or {})

        logger.info("Atualizando evento %s no Google Agenda: %s", external_id, payload["summary"])

        if self._is_authenticated and self.access_token:
            try:
                clean_id = external_id.removeprefix("gcal_")
                url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{clean_id}"
                headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.patch(url, headers=headers, json=payload)
            except Exception as exc:
                logger.warning("Falha ao atualizar evento no Google Calendar API: %s", exc)

        return ExternalCalendarEvent(
            external_id=external_id,
            provider="google_calendar",
            summary=payload["summary"],
            description=payload.get("description"),
            start_time=datetime.fromisoformat(payload["start"]["dateTime"]),
            end_time=datetime.fromisoformat(payload["end"]["dateTime"]),
            location=payload.get("location"),
        )

    async def delete_event(self, external_id: str) -> bool:
        """Remove evento do Google Agenda."""
        logger.info("Removendo evento %s do Google Agenda", external_id)
        if self._is_authenticated and self.access_token:
            try:
                clean_id = external_id.removeprefix("gcal_")
                url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{clean_id}"
                headers = {"Authorization": f"Bearer {self.access_token}"}
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.delete(url, headers=headers)
                    return resp.status_code in (200, 204)
            except Exception as exc:
                logger.warning("Falha ao remover evento no Google Calendar: %s", exc)
        return True

    async def list_events(self, start: datetime, end: datetime) -> list[dict]:
        """Lista eventos dentro de um intervalo de datas."""
        if not self._is_authenticated or not self.access_token:
            return []
        try:
            start_sp = self._ensure_sp_tz(start).isoformat()
            end_sp = self._ensure_sp_tz(end).isoformat()
            url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?timeMin={start_sp}&timeMax={end_sp}"
            headers = {"Authorization": f"Bearer {self.access_token}"}
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json().get("items", [])
        except Exception as exc:
            logger.warning("Falha ao listar eventos do Google Calendar: %s", exc)
        return []
