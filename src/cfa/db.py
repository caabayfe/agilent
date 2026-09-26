"""Async Postgres connection pool helper."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

type Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


@asynccontextmanager
async def open_pool(conninfo: str, *, max_size: int = 5) -> AsyncIterator[Pool]:
    pool: Pool = AsyncConnectionPool(
        conninfo,
        connection_class=AsyncConnection[DictRow],
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    await pool.open(wait=True, timeout=30)
    try:
        yield pool
    finally:
        await pool.close()
