"""Script de Healthcheck / Smoke Test do Meu Assistente (v0.3.0-RC).

Executa uma verificação rápida e sub-segundo (< 0.2s) de todos os subsistemas:
1. Conexão e criação de tabelas no banco relacional + Gestão de Contatos
2. Motor determinístico de fusos, detecção de sobreposição e viabilidade logística (Google Maps)
3. Esquemas estruturados e cliente da camada de IA (Gemini)
4. Formatação de Briefing Executivo Matinal
5. Rotas do servidor FastAPI, Webhook WhatsApp e Web API v1 / Dashboard
"""

import asyncio
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Garante suporte a UTF-8 no terminal Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from app.ai.schemas import AIInterpretationResult
from app.db.models import EventModel
from app.db.repository import ChangeLogRepository, ContactRepository, EventRepository, UserRepository
from app.db.session import Base
from app.domain.entities import Event, Location
from app.domain.enums import ConfidenceLevel, EventStatus, IntentType
from app.domain.formatters import ExecutiveBriefingFormatter
from app.engine.conflict import ConflictDetector
from app.engine.validator import DeterministicValidator
from app.main import app


async def run_smoke_test() -> bool:
    print("\n🔍 Iniciando Smoke Test do Meu Assistente (v0.3.0-RC)...")
    start_time = datetime.now()

    # 1. Teste de Banco de Dados, Contatos e Auditoria
    print("  [1/5] Verificando Banco de Dados, Contatos e Auditoria...", end=" ")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user_repo = UserRepository(session)
        event_repo = EventRepository(session)
        contact_repo = ContactRepository(session)
        changelog_repo = ChangeLogRepository(session)

        user = await user_repo.get_or_create("5511999990000", "Smoke Test User")
        assert user.id is not None

        # Contato
        contact = await contact_repo.create(
            owner_user_id=user.id,
            name="Contato Teste",
            phone="5511999991234",
            category="Teste",
        )
        assert contact.id is not None

        # Evento
        tz = ZoneInfo("America/Sao_Paulo")
        now = datetime.now(tz)
        event = await event_repo.create_event(
            owner_user_id=user.id,
            title="Smoke Test Event",
            start_time=now,
            end_time=now + timedelta(hours=1),
            contact_id=contact.id,
            travel_time_minutes=25,
            status=EventStatus.CONFIRMED.value,
        )
        assert event.id is not None

        history = await changelog_repo.get_history_for_entity("event", event.id)
        assert len(history) == 1
    print("OK ✅")

    # 2. Teste do Motor Determinístico e Viabilidade Logística (Google Maps)
    print("  [2/5] Verificando Motor Determinístico e Logística de Trânsito...", end=" ")
    t_start = datetime(2026, 10, 22, 14, 0, tzinfo=tz)
    t_end = datetime(2026, 10, 22, 15, 0, tzinfo=tz)

    # Conflito de sobreposição direta
    assert ConflictDetector.check_overlap(t_start, t_end, t_start + timedelta(minutes=30), t_end + timedelta(minutes=30)) is True
    assert ConflictDetector.check_overlap(t_start, t_end, t_end, t_end + timedelta(hours=1)) is False

    # Viabilidade logística
    prev_ev = EventModel(
        id=10,
        owner_user_id=user.id,
        title="Evento 1",
        start_time=t_start,
        end_time=t_end,
        status="confirmed",
        location_name="Centro",
    )
    risk_report = ConflictDetector.check_travel_feasibility(
        candidate_start=t_end + timedelta(minutes=15),
        candidate_end=t_end + timedelta(hours=1),
        candidate_location="Barra",
        existing_events=[prev_ev],
        travel_minutes=40,
    )
    assert risk_report is not None
    assert risk_report.has_conflict is True
    print("OK ✅")

    # 3. Teste da Camada de IA e Esquemas Tipados
    print("  [3/5] Verificando Camada de IA e Esquemas Tipados...", end=" ")
    sample = AIInterpretationResult(
        intent=IntentType.CREATE_EVENT,
        title="Consulta Teste",
        start_datetime="2026-10-22T14:00:00",
        location="Av Paulista 1000",
        confidence=ConfidenceLevel.HIGH,
    )
    validated = DeterministicValidator.validate_interpretation(sample, "America/Sao_Paulo")
    assert validated.is_valid is True
    assert validated.duration_inferred is True
    assert validated.location_address == "Av Paulista 1000"
    print("OK ✅")

    # 4. Teste de Formatação de Briefing Executivo
    print("  [4/5] Verificando Formatador de Briefing Executivo...", end=" ")
    ev_domain = Event(
        id=1,
        owner_user_id=user.id,
        title="Check-in Executivo",
        start_time=t_start,
        end_time=t_end,
        location=Location(raw_address="Paulista"),
        travel_time_minutes=20,
    )
    briefing = ExecutiveBriefingFormatter.format_briefing(user.display_name, t_start, [ev_domain])
    assert "Briefing Executivo" in briefing
    assert "Check-in Executivo" in briefing
    assert "20 min" in briefing
    print("OK ✅")

    # 5. Teste das Rotas FastAPI, Webhook WhatsApp e Web API v1
    print("  [5/5] Verificando Endpoints FastAPI, Webhook e Web API...", end=" ")
    def get_paths(routes_list):
        paths = []
        for r in routes_list:
            if hasattr(r, "path") and r.path:
                paths.append(r.path)
            if hasattr(r, "original_router") and hasattr(r.original_router, "routes"):
                paths.extend(get_paths(r.original_router.routes))
            elif hasattr(r, "routes"):
                paths.extend(get_paths(r.routes))
        return paths

    routes = get_paths(app.routes)
    assert "/" in routes
    assert "/webhook" in routes
    assert "/painel" in routes
    assert "/api/v1/events" in routes
    assert "/api/v1/contacts" in routes
    assert "/api/v1/briefing" in routes
    print("OK ✅")

    await engine.dispose()

    elapsed = (datetime.now() - start_time).total_seconds()
    print("-" * 80)
    print(f"✨ Smoke Test concluído com sucesso em {elapsed:.3f}s! Todos os 5 subsistemas operacionais.\n")
    return True


if __name__ == "__main__":
    success = asyncio.run(run_smoke_test())
    sys.exit(0 if success else 1)
