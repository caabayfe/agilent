"""Database migrations: ``python -m cfa.migrate [upgrade|current|history]``.

Runs once per deploy, as the ``migrate`` job, before any service starts:

1. Alembic upgrades the platform schema (data products, roles, audit, registry).
2. LangGraph's Postgres checkpointer runs its own versioned ``setup()`` inside
   the ``langgraph`` schema, so services never need DDL rights.
"""

import logging
import sys
from importlib.resources import files

import psycopg
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from cfa.config import MigrationSettings
from cfa.db import CHECKPOINT_SCHEMA, checkpoint_conninfo_kwargs
from cfa.logging import configure_logging

log = logging.getLogger("cfa.migrate")


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("cfa") / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("postgresql://", "postgresql+psycopg://", 1))
    return config


def setup_checkpointer(url: str) -> None:
    with psycopg.connect(url, row_factory=dict_row, **checkpoint_conninfo_kwargs()) as conn:
        PostgresSaver(conn).setup()
    log.info("langgraph checkpointer tables ready in schema %s", CHECKPOINT_SCHEMA)


def main(argv: list[str]) -> None:
    configure_logging()
    url = MigrationSettings().migrate_database_url.get_secret_value()
    config = alembic_config(url)
    match argv[:1]:
        case [] | ["upgrade"]:
            command.upgrade(config, "head")
            setup_checkpointer(url)
        case ["current"]:
            command.current(config, verbose=True)
        case ["history"]:
            command.history(config, verbose=True)
        case _:
            raise SystemExit("usage: python -m cfa.migrate [upgrade|current|history]")


if __name__ == "__main__":
    main(sys.argv[1:])
