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


def _sync_sqlite_schema(sync_conn) -> None:
    """Para desenvolvimento local com SQLite, adiciona colunas ausentes em tabelas pré-existentes.
    Mantém o PostgreSQL e os modelos de produção 100% intocados e puros.
    """
    if sync_conn.dialect.name != "sqlite":
        return

    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())

    for table_name, table in Base.metadata.tables.items():
        if table_name in existing_tables:
            existing_cols = {col["name"] for col in inspector.get_columns(table_name)}
            for col in table.columns:
                if col.name not in existing_cols:
                    col_type = col.type.compile(sync_conn.dialect)
                    default_clause = ""
                    if col.default is not None and hasattr(col.default, "arg") and col.default.arg is not None:
                        val = col.default.arg
                        if isinstance(val, bool):
                            default_clause = f" DEFAULT {1 if val else 0}"
                        elif isinstance(val, (int, float)):
                            default_clause = f" DEFAULT {val}"
                        elif isinstance(val, str):
                            default_clause = f" DEFAULT '{val}'"
                    elif col.server_default is not None and hasattr(col.server_default, "arg"):
                        default_clause = f" DEFAULT {col.server_default.arg}"
                    elif not col.nullable:
                        if "INT" in str(col_type).upper():
                            default_clause = " DEFAULT 0"
                        elif "CHAR" in str(col_type).upper() or "TEXT" in str(col_type).upper():
                            default_clause = " DEFAULT ''"
                        elif "BOOL" in str(col_type).upper():
                            default_clause = " DEFAULT 0"
                    sql = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}{default_clause}"
                    sync_conn.execute(text(sql))


async def init_db() -> None:
    """Cria todas as tabelas no banco de dados e compatibiliza colunas no SQLite local."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await conn.run_sync(_sync_sqlite_schema)


async def close_db() -> None:
    """Encerra as conexões do pool de conexão da engine."""
    await engine.dispose()
