"""Configurações e fixtures para testes assíncronos com pytest."""

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.ai.schemas import AIInterpretationResult
from app.db.session import Base
from app.domain.enums import ConfidenceLevel, IntentType


class MockGeminiInterpreter:
    """Mock da camada de IA para testes rápidos, determinísticos e 100% offline."""

    def __init__(self, predefined_response: AIInterpretationResult | None = None):
        self.predefined_response = predefined_response
        self.call_history: list[dict] = []

    def set_response(self, response: AIInterpretationResult) -> None:
        self.predefined_response = response

    async def interpret(
        self,
        raw_message: str,
        anchor_datetime: datetime,
        user_timezone: str = "America/Sao_Paulo",
        recent_context: str | None = None,
    ) -> AIInterpretationResult:
        self.call_history.append({
            "raw_message": raw_message,
            "anchor_datetime": anchor_datetime,
            "user_timezone": user_timezone,
            "recent_context": recent_context,
        })
        if self.predefined_response:
            return self.predefined_response

        # Resposta padrão segura
        return AIInterpretationResult(
            intent=IntentType.UNKNOWN,
            confidence=ConfidenceLevel.LOW,
            extracted_facts=[raw_message],
        )


@pytest.fixture
def mock_interpreter() -> MockGeminiInterpreter:
    """Fixture que fornece o interpretador mockado."""
    return MockGeminiInterpreter()


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Cria banco de dados SQLite assíncrono em memória para testes isolados."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
