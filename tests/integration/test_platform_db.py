"""Platform database guarantees: tamper-evident audit and durable conversation memory."""

import os
import uuid
from typing import Any, TypedDict

import psycopg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from cfa.audit_verify import verify_database
from cfa.db import checkpoint_conninfo_kwargs, open_pool


def dsn(role: str, password_env: str) -> str:
    return f"postgresql://{role}:{os.environ[password_env]}@postgres:5432/cfa"


ADMIN = dsn("cfa_admin", "POSTGRES_PASSWORD")
PLATFORM = dsn("cfa_platform", "DB_PASSWORD_PLATFORM")
EVAL = dsn("cfa_eval", "DB_PASSWORD_EVAL")


def test_live_audit_chain_is_intact() -> None:
    with psycopg.connect(EVAL) as conn:
        report = verify_database(conn)
    assert report.ok, report.breaks[:3]
    assert report.events > 0


def test_superuser_edit_that_bypasses_the_triggers_is_detected() -> None:
    with psycopg.connect(ADMIN) as conn:  # non-autocommit: everything below is rolled back
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "UPDATE audit.events SET reason = 'nothing to see' WHERE seq = (SELECT max(seq) FROM audit.events)"
        )
        report = verify_database(conn)
        conn.rollback()
    assert not report.ok
    assert report.breaks[-1].problem.startswith("content altered")


def test_audit_rows_cannot_be_updated_or_deleted() -> None:
    with psycopg.connect(EVAL) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM audit.events")
    with psycopg.connect(ADMIN) as conn, pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("UPDATE audit.events SET decision = 'allow'")


class Counter(TypedDict):
    turns: int


def counter_graph(saver: AsyncPostgresSaver) -> Any:
    graph = StateGraph(Counter)
    graph.add_node("turn", lambda state: {"turns": state["turns"] + 1})
    graph.add_edge(START, "turn")
    graph.add_edge("turn", END)
    return graph.compile(checkpointer=saver)


async def test_conversation_state_survives_a_new_process() -> None:
    config: Any = {"configurable": {"thread_id": f"it-{uuid.uuid4().hex}"}}
    async with open_pool(PLATFORM, **checkpoint_conninfo_kwargs()) as pool:
        await counter_graph(AsyncPostgresSaver(pool)).ainvoke({"turns": 0}, config)
    # A fresh pool and saver (as after a BFF restart) sees the same thread.
    async with open_pool(PLATFORM, **checkpoint_conninfo_kwargs()) as pool:
        state = await counter_graph(AsyncPostgresSaver(pool)).aget_state(config)
    assert state.values == {"turns": 1}


def test_the_app_role_has_no_ddl_rights_on_checkpoints() -> None:
    with psycopg.connect(PLATFORM) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("CREATE TABLE langgraph.sneaky (id int)")
