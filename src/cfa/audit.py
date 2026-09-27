"""Append-only, tamper-evident audit and lineage log.

One row per action (route decision, model call, tool call, access decision,
final answer), all sharing a ``trace_id`` (the OpenTelemetry trace id) and each
carrying the ``span_id`` that produced it. Together they let a third party
reconstruct what the agent saw, which model answered and who was accountable.

The database chains every row to the previous one (``prev_hash`` -> ``hash``,
migration 0004); ``verify_chain`` recomputes that chain independently.
"""

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from cfa.db import Pool
from cfa.telemetry import span_id_hex


class Action(StrEnum):
    ROUTE = "route"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    ACCESS_DECISION = "access_decision"
    ANSWER = "answer"


class AuditEvent(BaseModel):
    trace_id: str
    span_id: str | None = None
    component: str
    action: Action
    name: str | None = None
    actor_user: str | None = None
    actor_agent: str | None = None
    decision: str | None = None
    reason: str | None = None
    model_alias: str | None = None
    model_resolved: str | None = None
    provider: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    output_hash: str | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    policy_version: str | None = None


class StoredAuditEvent(AuditEvent):
    event_id: str
    ts: datetime
    seq: int
    prev_hash: str
    hash: str


def content_hash(value: Any) -> str:
    """Stable SHA-256 of any JSON-serialisable value (used for lineage)."""
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


_COLUMNS = list(AuditEvent.model_fields)
_INSERT = (
    f"INSERT INTO audit.events ({', '.join(_COLUMNS)}) "  # noqa: S608 - column names are static model fields
    f"VALUES ({', '.join(['%s'] * len(_COLUMNS))})"
)
_SELECT_TRACE = (
    f"SELECT event_id::text AS event_id, ts, seq, prev_hash, hash, {', '.join(_COLUMNS)} "  # noqa: S608 - static
    "FROM audit.events WHERE trace_id = %s ORDER BY seq"
)


class AuditLog:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def record(self, event: AuditEvent) -> None:
        """Persist one event. Failures propagate: an action that cannot be audited
        must not silently proceed. The database assigns seq, ts and the hash link."""
        if event.span_id is None:
            event = event.model_copy(update={"span_id": span_id_hex()})
        values = [
            Jsonb(value) if isinstance(value, dict) else value
            for value in (getattr(event, column) for column in _COLUMNS)
        ]
        async with self._pool.connection() as conn:
            await conn.execute(_INSERT, values)

    async def for_trace(self, trace_id: str) -> list[StoredAuditEvent]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(_SELECT_TRACE, (trace_id,))
            rows = await cursor.fetchall()
        return [StoredAuditEvent.model_validate(row) for row in rows]
