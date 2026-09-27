"""Async Postgres connection pool helper."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

type Pool = AsyncConnectionPool[AsyncConnection[DictRow]]

# LangGraph's checkpointer uses unqualified table names; they live in their own schema.
CHECKPOINT_SCHEMA = "langgraph"


def checkpoint_conninfo_kwargs() -> dict[str, Any]:
    """Connection settings LangGraph's Postgres checkpointer requires."""
    return {"autocommit": True, "prepare_threshold": 0, "options": f"-c search_path={CHECKPOINT_SCHEMA}"}


@asynccontextmanager
async def open_pool(conninfo: str, *, max_size: int = 5, **kwargs: Any) -> AsyncIterator[Pool]:
    pool: Pool = AsyncConnectionPool(
        conninfo,
        connection_class=AsyncConnection[DictRow],
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True, **kwargs},
    )
    await pool.open(wait=True, timeout=30)
    try:
        yield pool
    finally:
        await pool.close()
