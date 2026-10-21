"""Gerenciador de sessões e conexão assíncrona com o banco de dados."""

from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Classe base declarativa para todos os modelos ORM."""
    pass


def get_normalized_database_url(raw_url: str) -> str:
    """Normaliza URLs de conexão para os drivers assíncronos compatíveis."""
    if raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+asyncpg://"):
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw_url.startswith("sqlite://") and not raw_url.startswith("sqlite+aiosqlite://"):
        return raw_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return raw_url


database_url = get_normalized_database_url(settings.DATABASE_URL)

# Criação da engine assíncrona
engine: AsyncEngine = create_async_engine(
    database_url,
    echo=settings.DEBUG,
    future=True,
)

# Fábrica de sessões assíncronas
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Gerador de sessão assíncrona para injeção de dependência."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Cria todas as tabelas no banco de dados (útil para desenvolvimento local e testes)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Encerra as conexões do pool de conexão da engine."""
    await engine.dispose()
