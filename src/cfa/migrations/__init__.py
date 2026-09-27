"""Alembic migrations for the platform database (run by ``python -m cfa.migrate``).

Revisions are plain SQL: the schema is the contract between skills, the audit
trail and the eval harness, so it is written down explicitly rather than derived
from ORM models (there are none).
"""

from typing import Any, cast

import psycopg
from alembic import op


def driver_connection() -> psycopg.Connection[Any]:
    """The raw psycopg connection inside Alembic's transaction."""
    return cast("psycopg.Connection[Any]", op.get_bind().connection.driver_connection)


def run_sql(sql: str) -> None:
    """Run a multi-statement SQL script verbatim (no bind-parameter parsing)."""
    driver_connection().execute(sql.encode())
