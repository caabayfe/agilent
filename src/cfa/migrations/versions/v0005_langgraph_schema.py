"""Schema for LangGraph's Postgres checkpointer (conversation memory).

The checkpointer tables themselves are created by the library's own, versioned
``setup()`` (run by ``python -m cfa.migrate`` right after Alembic), so upgrading
``langgraph-checkpoint-postgres`` brings its migrations with it. This revision
only owns the boundary: a dedicated schema in the same database and least
privilege for the BFF, which can read/write checkpoints but has no DDL rights.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from cfa.migrations import run_sql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = """
CREATE SCHEMA langgraph;
GRANT USAGE ON SCHEMA langgraph TO cfa_platform;
ALTER DEFAULT PRIVILEGES IN SCHEMA langgraph GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cfa_platform;
ALTER DEFAULT PRIVILEGES IN SCHEMA langgraph GRANT USAGE, SELECT ON SEQUENCES TO cfa_platform;
"""


def upgrade() -> None:
    run_sql(SQL)


def downgrade() -> None:
    run_sql("DROP SCHEMA langgraph CASCADE")
