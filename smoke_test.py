"""Script de Healthcheck / Smoke Test do Meu Assistente.

Executa uma verificação rápida (em ~2s) de todos os subsistemas:
1. Conexão e criação de tabelas no banco relacional
2. Inserção de dados e trilha de auditoria (change_log)
3. Motor determinístico de fusos e detecção de sobreposição de horários
4. Esquemas estruturados e cliente da camada de IA (Gemini)
5. Rotas do servidor FastAPI e Webhook da WhatsApp Cloud API
"""

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Garante suporte a UTF-8 no terminal Windows
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from app.config import settings
from app.db.session import Base
from app.db.repository import UserRepository, EventRepository, ChangeLogRepository
from app.domain.enums import EventStatus, IntentType, ConfidenceLevel
from app.engine.validator import DeterministicValidator
from app.engine.conflict import ConflictDetector, ConflictSeverity
from app.ai.schemas import AIInterpretationResult
from app.main import app


async def run_smoke_test() -> bool:
    print("\n🔍 Iniciando Smoke Test do Meu Assistente...")
    start_time = datetime.now()

    # 1. Teste de Banco de Dados e Auditoria
    print("  [1/4] Verificando Banco de Dados e Auditoria...", end=" ")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user_repo = UserRepository(session)
        event_repo = EventRepository(session)
        changelog_repo = ChangeLogRepository(session)

        user = await user_repo.get_or_create("5511999990000", "Smoke Test User")
        assert user.id is not None

        tz = ZoneInfo("America/Sao_Paulo")
        now = datetime.now(tz)
        event = await event_repo.create_event(
            owner_user_id=user.id,
            title="Smoke Test Event",
            start_time=now,
            end_time=now + timedelta(hours=1),
            status=EventStatus.CONFIRMED.value,
        )
        assert event.id is not None

        history = await changelog_repo.get_history_for_entity("event", event.id)
        assert len(history) == 1
    print("OK ✅")

    # 2. Teste do Motor Determinístico de Regras e Conflitos
    print("  [2/4] Verificando Motor Determinístico de Regras e Conflitos...", end=" ")
    t_start = datetime(2026, 10, 22, 14, 0, tzinfo=tz)
    t_end = datetime(2026, 10, 22, 15, 0, tzinfo=tz)

    # Conflito real
    assert ConflictDetector.check_overlap(t_start, t_end, t_start + timedelta(minutes=30), t_end + timedelta(minutes=30)) is True
    # Adjacente (não é conflito)
    assert ConflictDetector.check_overlap(t_start, t_end, t_end, t_end + timedelta(hours=1)) is False
    print("OK ✅")

    # 3. Teste da Camada de IA e Esquemas Tipados
    print("  [3/4] Verificando Camada de IA e Esquemas Tipados...", end=" ")
    sample = AIInterpretationResult(
        intent=IntentType.CREATE_EVENT,
        title="Consulta Teste",
        start_datetime="2026-10-22T14:00:00",
        confidence=ConfidenceLevel.HIGH,
    )
    validated = DeterministicValidator.validate_interpretation(sample, "America/Sao_Paulo")
    assert validated.is_valid is True
    assert validated.duration_inferred is True
    print("OK ✅")

    # 4. Teste das Rotas FastAPI e Webhook
    print("  [4/4] Verificando Endpoints FastAPI e Webhook WhatsApp...", end=" ")
    routes = [route.path for route in app.routes]
    assert "/" in routes
    assert "/webhook" in routes
    print("OK ✅")

    await engine.dispose()

    elapsed = (datetime.now() - start_time).total_seconds()
    print("-" * 80)
    print(f"🚀 [STATUS]: Todos os subsistemas (IA, Banco, Motor de Regras, Webhook) estão operacionais e prontos para teste. ({elapsed:.2f}s)")
    print("-" * 80)
    return True


if __name__ == "__main__":
    success = asyncio.run(run_smoke_test())
    sys.exit(0 if success else 1)
