"""Least-privilege database roles, one per trust domain.

Passwords come from the environment (.env); nothing secret is stored in this
repository or in the migration history.

Revision ID: 0002
Revises: 0001
"""

import os
from collections.abc import Sequence

from psycopg import sql

from cfa.migrations import driver_connection, run_sql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = {
    "cfa_orders": "DB_PASSWORD_ORDERS",
    "cfa_billing": "DB_PASSWORD_BILLING",
    "cfa_service": "DB_PASSWORD_SERVICE",
    "cfa_platform": "DB_PASSWORD_PLATFORM",
    "cfa_eval": "DB_PASSWORD_EVAL",
}

GRANTS = """
REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- Every component may append audit events; nobody may update or delete them.
GRANT USAGE ON SCHEMA audit TO cfa_orders, cfa_billing, cfa_service, cfa_platform, cfa_eval;
GRANT INSERT ON audit.events TO cfa_orders, cfa_billing, cfa_service, cfa_platform, cfa_eval;

-- Data products: each skill reads only its own schema.
GRANT USAGE ON SCHEMA orders TO cfa_orders;
GRANT SELECT ON ALL TABLES IN SCHEMA orders TO cfa_orders;
GRANT USAGE ON SCHEMA billing TO cfa_billing;
GRANT SELECT ON ALL TABLES IN SCHEMA billing TO cfa_billing;
GRANT USAGE ON SCHEMA service TO cfa_service;
GRANT SELECT ON ALL TABLES IN SCHEMA service TO cfa_service;

-- Platform (IdP + BFF): identity directory, audit read-back, registry read.
GRANT USAGE ON SCHEMA identity, registry TO cfa_platform;
GRANT SELECT ON ALL TABLES IN SCHEMA identity TO cfa_platform;
GRANT SELECT ON audit.events TO cfa_platform;
GRANT SELECT ON registry.assets TO cfa_platform;

-- Eval harness: separate trust domain that reads ground truth and records decisions.
GRANT USAGE ON SCHEMA orders, billing, service, identity, registry TO cfa_eval;
GRANT SELECT ON ALL TABLES IN SCHEMA orders, billing, service, identity TO cfa_eval;
GRANT SELECT ON audit.events TO cfa_eval;
GRANT SELECT, INSERT ON registry.assets TO cfa_eval;
GRANT USAGE ON SEQUENCE registry.assets_id_seq TO cfa_eval;
"""


def upgrade() -> None:
    conn = driver_connection()
    for role, env in ROLES.items():
        password = os.environ.get(env)
        if not password:
            raise RuntimeError(f"{env} is not set: run `make init`")
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
    run_sql(GRANTS)


def downgrade() -> None:
    run_sql("REASSIGN OWNED BY cfa_orders, cfa_billing, cfa_service, cfa_platform, cfa_eval TO CURRENT_USER")
    run_sql("DROP OWNED BY cfa_orders, cfa_billing, cfa_service, cfa_platform, cfa_eval")
    for role in ROLES:
        driver_connection().execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
