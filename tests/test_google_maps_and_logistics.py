"""Testes unitários para inteligência de deslocamento, Google Maps e conflitos logísticos."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.google_maps import GoogleMapsAdapter
from app.db.models import EventModel, ReminderModel
from app.db.repository import EventRepository, ReminderRepository
from app.domain.enums import EventStatus
from app.domain.interfaces import TravelEstimate
from app.engine.conflict import ConflictDetector, ConflictSeverity
from app.services.reminder_dispatcher import ReminderDispatcherService


@pytest.mark.asyncio
async def test_google_maps_adapter_offline_fallback():
    """Testa estimador determinístico offline quando a chave não está configurada."""
    adapter = GoogleMapsAdapter(api_key="")
    estimate: TravelEstimate = await adapter.estimate_travel_time(
        origin="Centro, Rio de Janeiro",
        destination="Barra da Tijuca, Rio de Janeiro",
    )
    assert estimate.duration_minutes > 0
    assert estimate.is_approximate is True
    assert estimate.distance_km is not None
    assert "min" in (estimate.summary or "")


@pytest.mark.asyncio
async def test_google_maps_adapter_mocked_http_api():
    """Testa parse de resposta da Distance Matrix API quando autenticado."""
    adapter = GoogleMapsAdapter(api_key="valid_test_key")

    mock_resp = {
        "status": "OK",
        "rows": [
            {
                "elements": [
                    {
                        "status": "OK",
                        "duration": {"value": 2400, "text": "40 mins"},
                        "distance": {"value": 15000, "text": "15 km"},
                    }
                ]
            }
        ],
    }

    from unittest.mock import MagicMock
    mock_http_response = MagicMock()
    mock_http_response.status_code = 200
    mock_http_response.json.return_value = mock_resp

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_http_response):
        estimate = await adapter.estimate_travel_time(
            origin="Copacabana",
            destination="Ipanema",
            departure_time=datetime(2026, 10, 26, 10, 0, tzinfo=UTC),
        )
        assert estimate.duration_minutes == 40
        assert estimate.distance_km == 15.0
        assert estimate.is_approximate is False
        assert "40 min (15.0 km)" in (estimate.summary or "")


def test_conflict_detector_travel_feasibility_risk():
    """Testa detecção de risco de atraso quando o intervalo entre eventos é menor que o tempo de trânsito."""
    # Evento anterior: 13:00 às 14:00 no Centro
    prev_event = EventModel(
        id=1,
        owner_user_id=1,
        title="Reunião no Centro",
        start_time=datetime(2026, 10, 26, 13, 0),
        end_time=datetime(2026, 10, 26, 14, 0),
        status=EventStatus.CONFIRMED.value,
        location_name="Centro, RJ",
    )

    # Novo candidato: 14:15 às 15:00 na Barra (gap de 15 min, trânsito estimado de 45 min)
    cand_start = datetime(2026, 10, 26, 14, 15)
    cand_end = datetime(2026, 10, 26, 15, 0)

    report = ConflictDetector.check_travel_feasibility(
        candidate_start=cand_start,
        candidate_end=cand_end,
        candidate_location="Barra da Tijuca",
        existing_events=[prev_event],
        travel_minutes=45,
    )

    assert report is not None
    assert report.has_conflict is True
    assert report.severity == ConflictSeverity.LOGISTIC_RISK
    assert "Risco de atraso por deslocamento" in (report.warning_message or "")
    assert "intervalo de 15 min" in (report.warning_message or "")


def test_conflict_detector_travel_feasibility_ok():
    """Testa que intervalo suficiente (90 min para 45 min de viagem) não gera conflito."""
    prev_event = EventModel(
        id=1,
        owner_user_id=1,
        title="Reunião no Centro",
        start_time=datetime(2026, 10, 26, 13, 0),
        end_time=datetime(2026, 10, 26, 14, 0),
        status=EventStatus.CONFIRMED.value,
        location_name="Centro, RJ",
    )

    cand_start = datetime(2026, 10, 26, 15, 30)
    cand_end = datetime(2026, 10, 26, 16, 30)

    report = ConflictDetector.check_travel_feasibility(
        candidate_start=cand_start,
        candidate_end=cand_end,
        candidate_location="Barra da Tijuca",
        existing_events=[prev_event],
        travel_minutes=45,
    )

    assert report is None


@pytest.mark.asyncio
async def test_reminder_dispatcher_includes_travel_tip(db_session: AsyncSession):
    """Testa se o ReminderDispatcherService inclui dica de deslocamento quando disponível no evento."""
    # Cria evento com local e tempo de deslocamento
    event_repo = EventRepository(db_session)
    event = await event_repo.create_event(
        owner_user_id=1,
        title="Consulta com Cardiologista",
        start_time=datetime(2026, 10, 26, 16, 0),
        end_time=datetime(2026, 10, 26, 17, 0),
        location="Clínica São Vicente",
        travel_time_minutes=35,
        status=EventStatus.CONFIRMED.value,
    )

    # Cria lembrete pendente
    rem_repo = ReminderRepository(db_session)
    reminder = await rem_repo.create_reminder(
        owner_user_id=1,
        event_id=event.id,
        recipient_phone="5511999990001",
        message_text="Lembrete: Consulta com Cardiologista às 16h",
        send_at=datetime(2026, 10, 26, 15, 0),
    )

    # Carrega com relacionamento event
    reminder_model = await db_session.get(ReminderModel, reminder.id)
    reminder_model.event = event

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text_message.return_value = True

    dispatcher = ReminderDispatcherService(
        session_factory=None,
        whatsapp_client=mock_whatsapp,
    )

    success = await dispatcher._dispatch_single_reminder(reminder_model)
    assert success is True

    # Verifica se a dica de trânsito foi incluída na mensagem enviada
    mock_whatsapp.send_text_message.assert_called_once()
    sent_text = mock_whatsapp.send_text_message.call_args[1]["text"]
    assert "Deslocamento estimado" in sent_text
    assert "35 min" in sent_text


def test_conflict_detector_travel_feasibility_no_location():
    """Garante que eventos sem localização cadastrada não geram risco falso de trânsito."""
    prev_event = EventModel(
        id=1,
        owner_user_id=1,
        title="Reunião Remota / Zoom",
        start_time=datetime(2026, 10, 26, 14, 0),
        end_time=datetime(2026, 10, 26, 15, 0),
        status=EventStatus.CONFIRMED.value,
        location_name=None,
    )

    cand_start = datetime(2026, 10, 26, 15, 10)
    cand_end = datetime(2026, 10, 26, 16, 0)

    report = ConflictDetector.check_travel_feasibility(
        candidate_start=cand_start,
        candidate_end=cand_end,
        candidate_location=None,
        existing_events=[prev_event],
        travel_minutes=30,
    )
    assert report is None


def test_conflict_detector_travel_feasibility_different_days():
    """Garante que eventos em dias diferentes não geram conflito de tempo de viagem."""
    prev_event = EventModel(
        id=1,
        owner_user_id=1,
        title="Jantar no Rio",
        start_time=datetime(2026, 10, 25, 20, 0),
        end_time=datetime(2026, 10, 25, 22, 0),
        status=EventStatus.CONFIRMED.value,
        location_name="Copacabana, Rio de Janeiro",
    )

    cand_start = datetime(2026, 10, 26, 10, 0)
    cand_end = datetime(2026, 10, 26, 11, 0)

    report = ConflictDetector.check_travel_feasibility(
        candidate_start=cand_start,
        candidate_end=cand_end,
        candidate_location="Av Faria Lima, São Paulo",
        existing_events=[prev_event],
        travel_minutes=60,
    )
    assert report is None
