"""Tamper-evident audit trail, linked to OpenTelemetry traces.

* Hash chain: every event stores ``prev_hash`` (the previous event's hash) and its
  own ``hash`` = SHA-256 over a canonical form of the row, which includes
  ``prev_hash``. Editing, deleting or reordering any event breaks every later
  link; ``make verify-audit`` recomputes the chain independently.
* The chain is computed by the database, under an advisory lock, so concurrent
  writers in different processes (BFF, skill server, evals) can never fork it and
  no writer can choose its own hash.
* Append-only guard: UPDATE, DELETE and TRUNCATE are rejected even for roles that
  might one day be granted them. A superuser can still bypass triggers, which is
  exactly the case the hash chain exists to detect.
* ``span_id``: the OTel span that produced the event (``trace_id`` already is the
  W3C trace id), so each audit row links to its span in Jaeger / App Insights.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

from cfa.migrations import run_sql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQL = """
ALTER TABLE audit.events
    ADD COLUMN seq       BIGINT UNIQUE,
    ADD COLUMN span_id   TEXT,
    ADD COLUMN prev_hash TEXT,
    ADD COLUMN hash      TEXT;
ALTER TABLE audit.events
    ALTER COLUMN seq SET NOT NULL,
    ALTER COLUMN prev_hash SET NOT NULL,
    ALTER COLUMN hash SET NOT NULL;

-- Canonical, session-independent form of an event (timestamps as epoch microseconds,
-- so the TimeZone setting cannot change the digest). Every column except hash.
CREATE FUNCTION audit.canonical(e audit.events) RETURNS TEXT
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    SELECT jsonb_build_object(
        'seq', e.seq, 'prev_hash', e.prev_hash, 'event_id', e.event_id,
        'ts_us', (extract(epoch FROM e.ts) * 1000000)::bigint,
        'trace_id', e.trace_id, 'span_id', e.span_id,
        'component', e.component, 'action', e.action, 'name', e.name,
        'actor_user', e.actor_user, 'actor_agent', e.actor_agent,
        'decision', e.decision, 'reason', e.reason,
        'model_alias', e.model_alias, 'model_resolved', e.model_resolved, 'provider', e.provider,
        'input', e.input, 'output', e.output, 'output_hash', e.output_hash,
        'latency_ms', e.latency_ms, 'tokens_in', e.tokens_in, 'tokens_out', e.tokens_out,
        'policy_version', e.policy_version
    )::text
$$;

CREATE FUNCTION audit.chain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
DECLARE
    last_seq  BIGINT;
    last_hash TEXT;
BEGIN
    -- One chain, one writer at a time (released when the inserting transaction ends).
    PERFORM pg_advisory_xact_lock(hashtext('audit.events'));
    SELECT e.seq, e.hash INTO last_seq, last_hash FROM audit.events e ORDER BY e.seq DESC LIMIT 1;
    NEW.seq       := COALESCE(last_seq, 0) + 1;
    NEW.prev_hash := COALESCE(last_hash, 'sha256:genesis');
    NEW.ts        := clock_timestamp();
    NEW.hash      := 'sha256:' || encode(sha256(convert_to(audit.canonical(NEW), 'UTF8')), 'hex');
    RETURN NEW;
END
$$;

CREATE FUNCTION audit.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit.events is append-only (% rejected)', TG_OP;
END
$$;

CREATE TRIGGER events_chain BEFORE INSERT ON audit.events
    FOR EACH ROW EXECUTE FUNCTION audit.chain();
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON audit.events
    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON audit.events
    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation();

DROP INDEX audit.audit_events_trace_idx;
CREATE INDEX audit_events_trace_idx ON audit.events (trace_id, seq);

REVOKE ALL ON FUNCTION audit.chain() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit.canonical(audit.events) TO cfa_platform, cfa_eval;
"""


def upgrade() -> None:
    run_sql(SQL)


def downgrade() -> None:
    run_sql(
        """
        DROP TRIGGER events_no_truncate ON audit.events;
        DROP TRIGGER events_append_only ON audit.events;
        DROP TRIGGER events_chain ON audit.events;
        DROP FUNCTION audit.reject_mutation();
        DROP FUNCTION audit.chain();
        DROP FUNCTION audit.canonical(audit.events);
        ALTER TABLE audit.events DROP COLUMN hash, DROP COLUMN prev_hash, DROP COLUMN span_id, DROP COLUMN seq;
        """
    )
