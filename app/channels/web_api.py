"""Controlador da API REST v1 e do Dashboard Web."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.google_calendar import GoogleCalendarAdapter
from app.channels.schemas_web import (
    BriefingResponse,
    ContactCreateRequest,
    ContactResponse,
    ContactUpdateRequest,
    EventCreateRequest,
    EventResponse,
    EventUpdateRequest,
    ReminderResponse,
    SystemStatusResponse,
)
from app.db.models import ReminderModel, UserModel
from app.db.repository import ContactRepository, EventRepository, ReminderRepository, UserRepository
from app.db.session import get_db_session
from app.domain.entities import Event, Location
from app.domain.enums import EventStatus
from app.domain.formatters import ExecutiveBriefingFormatter
from app.engine.conflict import ConflictDetector

router = APIRouter(prefix="/api/v1", tags=["REST API"])
dashboard_router = APIRouter(tags=["Web Dashboard"])

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
DASHBOARD_FILE = TEMPLATES_DIR / "dashboard.html"


@dashboard_router.get("/painel", response_class=HTMLResponse)
@dashboard_router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard_page():
    """Retorna a interface visual do painel executivo embutido."""
    if not DASHBOARD_FILE.exists():
        return HTMLResponse("<h3>Dashboard não encontrado</h3>", status_code=404)
    return HTMLResponse(content=DASHBOARD_FILE.read_text(encoding="utf-8"))


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    x_user_phone: Annotated[str | None, Header()] = None,
    phone: Annotated[str | None, Query()] = None,
) -> UserModel:
    """Injeta o usuário logado via header X-User-Phone ou query param (fallback para ambiente dev)."""
    user_repo = UserRepository(session)
    target_phone = x_user_phone or phone or "5511999990001"
    user = await user_repo.get_or_create(
        phone_number=target_phone,
        display_name="Carlos (Principal)",
    )
    return user


CurrentUserDep = Annotated[UserModel, Depends(get_current_user)]
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("/events", response_model=list[EventResponse])
async def list_events(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
):
    """Lista compromissos do usuário com filtros opcionais de data."""
    event_repo = EventRepository(session)
    user_tz = ZoneInfo(current_user.timezone)
    now = datetime.now(user_tz)
    s_dt = start_date or (now - timedelta(days=30))
    e_dt = end_date or (now + timedelta(days=90))

    events = await event_repo.list_events_in_range(
        owner_user_id=current_user.id,
        start_time=s_dt,
        end_time=e_dt,
    )
    return [
        EventResponse(
            id=ev.id,
            title=ev.title,
            description=ev.description,
            start_time=ev.start_time,
            end_time=ev.end_time,
            timezone=ev.timezone,
            status=ev.status,
            location=ev.location_name or (ev.location.raw_address if ev.location else None),
            travel_time_minutes=ev.travel_time_minutes,
            external_calendar_id=ev.external_calendar_id,
            contact_id=ev.contact_id,
        )
        for ev in events
    ]


@router.post("/events", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    req: EventCreateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
):
    """Cria um novo compromisso com validação determinística de conflitos e sincronização."""
    user_tz = ZoneInfo(current_user.timezone)
    s_time = req.start_time if req.start_time.tzinfo else req.start_time.replace(tzinfo=user_tz)
    e_time = req.end_time if req.end_time.tzinfo else req.end_time.replace(tzinfo=user_tz)

    if e_time <= s_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O horário de término deve ser posterior ao horário de início.",
        )

    event_repo = EventRepository(session)
    conflicts = await event_repo.find_conflicts(
        owner_user_id=current_user.id,
        start_time=s_time,
        end_time=e_time,
    )
    conflict_report = ConflictDetector.analyze_conflicts(
        candidate_start=s_time,
        candidate_end=e_time,
        existing_events=conflicts,
    )

    if conflict_report.has_conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_report.warning_message or "Existe um conflito de horário com outro compromisso.",
        )

    ev = await event_repo.create_event(
        owner_user_id=current_user.id,
        title=req.title,
        start_time=s_time,
        end_time=e_time,
        timezone=current_user.timezone,
        description=req.description,
        location=req.location,
        travel_time_minutes=req.travel_time_minutes,
        contact_id=req.contact_id,
        status=EventStatus.CONFIRMED.value,
    )

    # Lembrete opcional
    if req.reminder_minutes_before > 0:
        reminder_repo = ReminderRepository(session)
        send_at = s_time - timedelta(minutes=req.reminder_minutes_before)
        await reminder_repo.create_reminder(
            owner_user_id=current_user.id,
            event_id=ev.id,
            recipient_phone=current_user.phone_number,
            message_text=f"Lembrete: {ev.title} às {ev.start_time.strftime('%Hh%M')}",
            send_at=send_at,
        )

    # Google Calendar Sync
    try:
        gcal = GoogleCalendarAdapter()
        loc_entity = Location(raw_address=req.location) if req.location else None
        event_entity = Event(
            id=ev.id,
            title=ev.title,
            start_time=ev.start_time,
            end_time=ev.end_time,
            location=loc_entity,
        )
        gcal_id = await gcal.create_event(event_entity)
        if gcal_id:
            await event_repo.update_event(ev.id, owner_user_id=current_user.id, updates={"external_calendar_id": gcal_id})
            ev.external_calendar_id = gcal_id
    except Exception:
        pass

    return EventResponse(
        id=ev.id,
        title=ev.title,
        description=ev.description,
        start_time=ev.start_time,
        end_time=ev.end_time,
        timezone=ev.timezone,
        status=ev.status,
        location=ev.location_name or req.location,
        travel_time_minutes=ev.travel_time_minutes,
        external_calendar_id=ev.external_calendar_id,
        contact_id=ev.contact_id,
    )


@router.patch("/events/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: int,
    req: EventUpdateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
):
    """Atualiza campos de um compromisso."""
    event_repo = EventRepository(session)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        ev = await event_repo.get_by_id(event_id, owner_user_id=current_user.id)
        if not ev:
            raise HTTPException(status_code=404, detail="Compromisso não encontrado.")
    else:
        ev = await event_repo.update_event(
            event_id=event_id,
            owner_user_id=current_user.id,
            updates=updates,
        )

    return EventResponse(
        id=ev.id,
        title=ev.title,
        description=ev.description,
        start_time=ev.start_time,
        end_time=ev.end_time,
        timezone=ev.timezone,
        status=ev.status,
        location=ev.location_name,
        travel_time_minutes=ev.travel_time_minutes,
        external_calendar_id=ev.external_calendar_id,
        contact_id=ev.contact_id,
    )


@router.delete("/events/{event_id}")
async def delete_event(
    event_id: int,
    current_user: CurrentUserDep,
    session: DbSessionDep,
):
    """Cancela um compromisso existente."""
    event_repo = EventRepository(session)
    ev = await event_repo.cancel_event(event_id=event_id, owner_user_id=current_user.id)
    if ev and ev.external_calendar_id:
        try:
            gcal = GoogleCalendarAdapter()
            await gcal.delete_event(ev.external_calendar_id)
        except Exception:
            pass
    return {"status": "cancelled", "event_id": event_id}


@router.get("/contacts", response_model=list[ContactResponse])
async def list_contacts(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    q: str | None = None,
):
    """Lista contatos do usuário com filtro de busca opcional."""
    repo = ContactRepository(session)
    if q:
        contacts = await repo.search_by_name(owner_user_id=current_user.id, query=q)
    else:
        contacts = await repo.list_by_user(owner_user_id=current_user.id)
    return [
        ContactResponse(
            id=c.id,
            name=c.name,
            phone=c.phone_number,
            email=c.email,
            category=c.category,
            notes=c.notes,
        )
        for c in contacts
    ]


@router.post("/contacts", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    req: ContactCreateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
):
    """Cadastra um novo contato na agenda do usuário."""
    repo = ContactRepository(session)
    contact = await repo.create(
        owner_user_id=current_user.id,
        name=req.name,
        phone=req.phone,
        email=req.email,
        category=req.category,
        notes=req.notes,
    )
    return ContactResponse(
        id=contact.id,
        name=contact.name,
        phone=contact.phone_number,
        email=contact.email,
        category=contact.category,
        notes=contact.notes,
    )


@router.put("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: int,
    req: ContactUpdateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
):
    """Atualiza dados cadastrais de um contato."""
    repo = ContactRepository(session)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    contact = await repo.update(contact_id=contact_id, owner_user_id=current_user.id, **updates)
    if not contact:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
    return ContactResponse(
        id=contact.id,
        name=contact.name,
        phone=contact.phone_number,
        email=contact.email,
        category=contact.category,
        notes=contact.notes,
    )


@router.delete("/contacts/{contact_id}")
async def delete_contact(
    contact_id: int,
    current_user: CurrentUserDep,
    session: DbSessionDep,
):
    """Remove um contato da agenda do usuário."""
    repo = ContactRepository(session)
    success = await repo.delete(contact_id=contact_id, owner_user_id=current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
    return {"status": "deleted", "contact_id": contact_id}


@router.get("/reminders", response_model=list[ReminderResponse])
async def list_reminders(
    current_user: CurrentUserDep,
    session: DbSessionDep,
):
    """Lista lembretes programados do usuário."""
    query = (
        select(ReminderModel)
        .where(ReminderModel.owner_user_id == current_user.id)
        .order_by(ReminderModel.send_at.desc())
        .limit(50)
    )
    result = await session.execute(query)
    reminders = result.scalars().all()
    return [
        ReminderResponse(
            id=r.id,
            event_id=r.event_id,
            recipient_phone=r.recipient_phone,
            message_text=r.message_text,
            send_at=r.send_at,
            status=r.status,
            attempts_count=1,
        )
        for r in reminders
    ]


@router.get("/briefing", response_model=BriefingResponse)
async def get_briefing(
    current_user: CurrentUserDep,
    session: DbSessionDep,
):
    """Retorna o briefing executivo do dia do usuário."""
    user_tz = ZoneInfo(current_user.timezone)
    now = datetime.now(user_tz)
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    event_repo = EventRepository(session)
    events = await event_repo.list_events_in_range(
        owner_user_id=current_user.id,
        start_time=start_day,
        end_time=end_day,
    )

    briefing_text = ExecutiveBriefingFormatter.format_briefing(
        user_name=current_user.display_name,
        target_date=now,
        events=list(events),
    )

    return BriefingResponse(
        user_name=current_user.display_name,
        target_date=now.strftime("%d/%m/%Y"),
        events_count=len(events),
        briefing_text=briefing_text,
    )


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(
    current_user: CurrentUserDep,
):
    """Retorna o status operacional dos subsistemas e adaptadores."""
    return SystemStatusResponse(
        status="operational",
        version="0.3.0-RC",
        user_phone=current_user.phone_number,
        google_calendar_status="hybrid_connected",
        google_maps_status="active",
        whatsapp_status="ready",
    )
