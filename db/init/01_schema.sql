-- One schema per data product. Each MCP skill connects with a role that can only
-- read its own schema (see 03_roles.sh), so the data-product boundary is enforced
-- by the database as well as by the skill's entitlement check.

CREATE SCHEMA identity;
CREATE SCHEMA orders;
CREATE SCHEMA billing;
CREATE SCHEMA service;
CREATE SCHEMA audit;
CREATE SCHEMA registry;

-- identity (mock IdP directory) -------------------------------------------------
CREATE TABLE identity.customers (
    customer_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL
);

CREATE TABLE identity.users (
    username     TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    title        TEXT NOT NULL,
    customer_id  TEXT NOT NULL REFERENCES identity.customers (customer_id),
    roles        TEXT[] NOT NULL
);

CREATE TABLE identity.agents (
    agent_id     TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    scopes       TEXT[] NOT NULL,
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- orders data product -------------------------------------------------------------
CREATE TABLE orders.orders (
    order_id    TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    placed_on   DATE NOT NULL,
    status      TEXT NOT NULL,
    total       NUMERIC(12, 2) NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'EUR'
);

CREATE TABLE orders.order_lines (
    order_id        TEXT NOT NULL REFERENCES orders.orders (order_id),
    line_no         INT NOT NULL,
    sku             TEXT NOT NULL,
    description     TEXT NOT NULL,
    quantity        INT NOT NULL,
    status          TEXT NOT NULL,
    tracking_number TEXT,
    eta             DATE,
    PRIMARY KEY (order_id, line_no)
);

-- billing data product ------------------------------------------------------------
CREATE TABLE billing.invoices (
    invoice_id  TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    order_id    TEXT NOT NULL,
    issued_on   DATE NOT NULL,
    due_on      DATE NOT NULL,
    total       NUMERIC(12, 2) NOT NULL,
    amount_paid NUMERIC(12, 2) NOT NULL DEFAULT 0,
    status      TEXT NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'EUR'
);

-- service data product ------------------------------------------------------------
CREATE TABLE service.instruments (
    serial         TEXT PRIMARY KEY,
    customer_id    TEXT NOT NULL,
    model          TEXT NOT NULL,
    name           TEXT NOT NULL,
    installed_on   DATE NOT NULL,
    warranty_until DATE NOT NULL
);

CREATE TABLE service.visits (
    visit_id   TEXT PRIMARY KEY,
    serial     TEXT NOT NULL REFERENCES service.instruments (serial),
    visit_date DATE NOT NULL,
    visit_type TEXT NOT NULL,
    summary    TEXT NOT NULL,
    engineer   TEXT NOT NULL
);

CREATE TABLE service.kb_articles (
    kb_id       TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    applies_to  TEXT NOT NULL,
    error_codes TEXT[] NOT NULL DEFAULT '{}',
    body        TEXT NOT NULL,
    escalate    BOOLEAN NOT NULL DEFAULT FALSE,
    search      TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', title || ' ' || applies_to || ' ' || body)
    ) STORED
);
CREATE INDEX kb_articles_search_idx ON service.kb_articles USING GIN (search);

-- audit / lineage (append-only: no role is granted UPDATE or DELETE) ---------------
CREATE TABLE audit.events (
    event_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    trace_id       TEXT NOT NULL,
    component      TEXT NOT NULL,
    action         TEXT NOT NULL,
    name           TEXT,
    actor_user     TEXT,
    actor_agent    TEXT,
    decision       TEXT,
    reason         TEXT,
    model_alias    TEXT,
    model_resolved TEXT,
    provider       TEXT,
    input          JSONB NOT NULL DEFAULT '{}'::jsonb,
    output         JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_hash    TEXT,
    latency_ms     INT,
    tokens_in      INT,
    tokens_out     INT,
    policy_version TEXT
);
CREATE INDEX audit_events_trace_idx ON audit.events (trace_id, ts);

-- asset registry (promotion decisions) ----------------------------------------------
CREATE TABLE registry.assets (
    id         BIGSERIAL PRIMARY KEY,
    asset_id   TEXT NOT NULL,
    decision   TEXT NOT NULL,
    risk_tier  INT NOT NULL,
    run_id     TEXT NOT NULL,
    mode       TEXT NOT NULL,
    bindings   JSONB NOT NULL,
    summary    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
