import asyncio
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from backend.app.core.config import DatabaseSettings



def create_database_engine(settings: DatabaseSettings) -> AsyncEngine:
    if not settings.sslrootcert.is_file():
        raise ValueError("RDS CA certificate missing; configure DB_SSLROOTCERT.")
    url = URL.create(
        "postgresql+psycopg",
        username=settings.user,
        password=settings.password.get_secret_value(),
        host=settings.host,
        port=settings.port,
        database=settings.name,
    )
    return create_async_engine(
        url,
        connect_args={
            "sslmode": "verify-full",
            "sslrootcert": str(settings.sslrootcert),
            "connect_timeout": settings.connect_timeout,
        },
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        pool_timeout=10,
        hide_parameters=True,
    )


@lru_cache
def get_engine() -> AsyncEngine:
    return create_database_engine(DatabaseSettings())


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(get_engine(), expire_on_commit=False) as session:
        yield session


async def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        get_engine.cache_clear()
