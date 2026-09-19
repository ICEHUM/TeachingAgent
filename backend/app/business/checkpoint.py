from __future__ import annotations

import os
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def enforce_checkpoint_security() -> None:
    value = os.environ.get("LANGGRAPH_STRICT_MSGPACK", "").lower()
    if value not in {"1", "true", "yes"}:
        raise RuntimeError("LANGGRAPH_STRICT_MSGPACK=true is required")


def create_checkpoint_pool(conninfo: str, *, open_immediately: bool = False) -> AsyncConnectionPool:
    enforce_checkpoint_security()
    return AsyncConnectionPool(
        conninfo=conninfo,
        open=open_immediately,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )


@asynccontextmanager
async def checkpoint_saver(conninfo: str):
    pool = create_checkpoint_pool(conninfo)
    await pool.open()
    try:
        yield AsyncPostgresSaver(pool)
    finally:
        await pool.close()
