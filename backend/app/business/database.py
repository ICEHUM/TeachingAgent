from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from . import BUSINESS_SCHEMA


def create_business_engine(url: str, *, sqlite_test_mode: bool = False) -> AsyncEngine:
    options = {"schema_translate_map": {BUSINESS_SCHEMA: None}} if sqlite_test_mode else None
    return create_async_engine(url, pool_pre_ping=True, execution_options=options)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
