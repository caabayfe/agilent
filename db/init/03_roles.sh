#!/usr/bin/env bash
# Least-privilege database roles, one per trust domain. Passwords come from the
# environment (.env); nothing secret is stored in this repository.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v orders_pw="$DB_PASSWORD_ORDERS" \
  -v billing_pw="$DB_PASSWORD_BILLING" \
  -v service_pw="$DB_PASSWORD_SERVICE" \
  -v platform_pw="$DB_PASSWORD_PLATFORM" \
  -v eval_pw="$DB_PASSWORD_EVAL" <<'SQL'
CREATE ROLE cfa_orders   LOGIN PASSWORD :'orders_pw';
CREATE ROLE cfa_billing  LOGIN PASSWORD :'billing_pw';
CREATE ROLE cfa_service  LOGIN PASSWORD :'service_pw';
CREATE ROLE cfa_platform LOGIN PASSWORD :'platform_pw';
CREATE ROLE cfa_eval     LOGIN PASSWORD :'eval_pw';

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
SQL
