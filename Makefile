# Customer Facing Assistant PoC. Run `make help` for targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
PSQL := $(COMPOSE) exec -T postgres psql -U cfa_admin -d cfa -v ON_ERROR_STOP=1
PROFILE ?= fake
SCOPE ?= billing.read

.PHONY: help init up down reset logs ps gateway swap-provider unswap-provider break-provider fix-provider \
        skills disable-skill enable-skill break-billing fix-billing break-mcp fix-mcp revoke-agent-scope restore-agent-scope \
        compare-providers verify-audit tamper-audit untamper-audit traces db-history eval eval-matrix eval-mutants grade-answer load-evidence check test ui-check

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

init: ## Create .env with fresh local secrets (model credentials left blank)
	@./scripts/init-env.sh
	@[[ -f gateway/active.yaml ]] || ./scripts/gateway.sh fake

up: init ## Build and start the whole stack (UI: http://localhost:5173)
	$(COMPOSE) up -d --build --wait
	@echo "UI  -> http://localhost:5173"
	@echo "API -> http://localhost:8000/healthz   (gateway profile: $$(cat gateway/active.profile))"
	@echo "Traces (Jaeger) -> http://localhost:16686"

down: ## Stop the stack (keeps data)
	$(COMPOSE) down

reset: ## Stop the stack and delete the database volume
	$(COMPOSE) down -v

logs: ## Tail logs
	$(COMPOSE) logs -f --tail=100

ps: ## Service status
	$(COMPOSE) ps

# ---------------------------------------------------------------- demo levers
gateway: ## Activate a gateway profile: make gateway PROFILE=fake|live|...
	@./scripts/gateway.sh $(PROFILE)

swap-provider: ## Re-point assistant-reasoning to the other deployment (gateway config only)
	@p=$$(cat gateway/active.profile); ./scripts/gateway.sh $${p%%-*}-swapped
	@git --no-pager diff --no-index --stat gateway/profiles/$$(cut -d- -f1 gateway/active.profile).yaml gateway/active.yaml || true

unswap-provider: ## Undo swap-provider / break-provider
	@p=$$(cat gateway/active.profile); ./scripts/gateway.sh $${p%%-*}

break-provider: ## Revoke the primary reasoning deployment's key; the gateway fails over
	@p=$$(cat gateway/active.profile); ./scripts/gateway.sh $${p%%-*}-broken

fix-provider: unswap-provider ## Restore the provider

compare-providers: ## Same evals on both sides of a provider swap, then diff config + behaviour: [A=live B=live-swapped REPEATS=3]
	@set -euo pipefail; orig=$$(cat gateway/active.profile); a=$(or $(A),$${orig%%-*}); b=$(or $(B),$${a}-swapped); \
	restore() { [[ "$$(cat gateway/active.profile)" == "$$orig" ]] || ./scripts/gateway.sh "$$orig" >/dev/null; }; \
	trap restore EXIT; \
	$(COMPOSE) build -q evals >/dev/null; first=1; \
	for p in "$$b" "$$a"; do \
	  [[ -n "$$first" ]] || { echo "   (waiting $(or $(COOLDOWN),60)s: the skill server's per-user rate-limit window must drain between runs)"; sleep $(or $(COOLDOWN),60); }; first=; \
	  echo "== eval on gateway profile $$p (agent unchanged)"; \
	  ./scripts/gateway.sh "$$p"; \
	  $(COMPOSE) run --rm --no-deps evals python -m evals.run --label "$$p" $(if $(REPEATS),--repeats $(REPEATS)) | grep -E '^\*\*Decision'; \
	done; \
	$(COMPOSE) run --rm --no-deps evals python -m evals.compare "$$a" "$$b"; \
	echo "report: evals/reports/compare-latest.md"

skills: ## Show the skills on the MCP server as a persona sees them: make skills [PERSONA=bob]
	@$(COMPOSE) exec -T api python -m cfa.skills.show $(or $(PERSONA),alice)

disable-skill: ## Kill switch for one skill, server stays up: make disable-skill SKILL=billing
	@test -n "$(SKILL)" || (echo "usage: make disable-skill SKILL=billing" && exit 1)
	@DISABLED_SKILLS=$(SKILL) $(COMPOSE) up -d --wait --no-deps --force-recreate mcp-customer
	@$(COMPOSE) exec -T mcp-customer python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"

enable-skill: ## Re-enable every skill
	@DISABLED_SKILLS= $(COMPOSE) up -d --wait --no-deps --force-recreate mcp-customer
	@$(COMPOSE) exec -T mcp-customer python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"

break-billing: ## Alias: switch the billing skill off (graceful degradation demo)
	@$(MAKE) --no-print-directory disable-skill SKILL=billing

fix-billing: enable-skill ## Alias: switch it back on

break-mcp: ## Stop the MCP server: retries, then the circuit breaker fails fast (resilience demo)
	@$(COMPOSE) stop mcp-customer

fix-mcp: ## Start it again; the breaker half-opens after its reset window and closes on success
	@$(COMPOSE) start mcp-customer && $(COMPOSE) up -d --wait --no-deps mcp-customer >/dev/null

revoke-agent-scope: ## Revoke a scope from the agent identity: make revoke-agent-scope SCOPE=billing.read
	@$(PSQL) -c "UPDATE identity.agents SET scopes = array_remove(scopes, '$(SCOPE)') WHERE agent_id = 'customer-assistant';" >/dev/null
	@$(PSQL) -tAc "SELECT 'agent scopes now: ' || array_to_string(scopes, ', ') FROM identity.agents WHERE agent_id = 'customer-assistant';"

restore-agent-scope: ## Restore a revoked agent scope: make restore-agent-scope SCOPE=billing.read
	@$(PSQL) -c "UPDATE identity.agents SET scopes = array_append(scopes, '$(SCOPE)') WHERE agent_id = 'customer-assistant' AND NOT ('$(SCOPE)' = ANY(scopes));" >/dev/null
	@$(PSQL) -tAc "SELECT 'agent scopes now: ' || array_to_string(scopes, ', ') FROM identity.agents WHERE agent_id = 'customer-assistant';"

# ---------------------------------------------------------------- audit & tracing
verify-audit: ## Recompute the audit hash chain (read-only role); non-zero exit if tampered
	@$(COMPOSE) exec -T api python -m cfa.audit_verify

# A rogue DBA hides a denial: bypasses the append-only triggers and flips the latest
# 'deny' to 'allow'. The hash chain is what catches it (make verify-audit).
tamper-audit: ## Demo: superuser silently rewrites the latest denial as 'allow'
	@$(PSQL) -qtAc "SET session_replication_role = replica; UPDATE audit.events SET decision = 'allow' WHERE seq = (SELECT max(seq) FROM audit.events WHERE decision = 'deny') RETURNING 'tampered event #' || seq || ' (' || component || ' ' || name || ', ' || reason || '): deny -> allow';" | grep . || echo "no denial to tamper with yet: ask as bob about invoices first"

untamper-audit: ## Undo tamper-audit (restore the flipped denials)
	@$(PSQL) -qtAc "SET session_replication_role = replica; UPDATE audit.events SET decision = 'deny' WHERE decision = 'allow' AND reason IN ('agent_scope_missing', 'user_role_missing', 'cross_tenant', 'not_found', 'skill_disabled') RETURNING 'restored event #' || seq;"

traces: ## Open the Jaeger UI (OpenTelemetry traces)
	@open http://localhost:16686 2>/dev/null || echo "Jaeger -> http://localhost:16686"

db-history: ## Alembic migration history and current revision
	@$(COMPOSE) run --rm --no-deps migrate python -m cfa.migrate history
	@$(COMPOSE) run --rm --no-deps migrate python -m cfa.migrate current

# ---------------------------------------------------------------- evals
eval: ## Baseline eval: gates, grader canaries, promotion decision
	$(COMPOSE) run --rm --no-deps evals python -m evals.run $(ARGS)

eval-matrix: ## Same eval suite on every model in the UI selector -> evals/reports/matrix-latest.md: [ONLY=gpt-4o,live REPEATS=3]
	$(COMPOSE) build -q evals >/dev/null
	$(COMPOSE) run --rm --no-deps evals python -m evals.matrix $(if $(ONLY),--only $(ONLY)) $(if $(REPEATS),--repeats $(REPEATS))

grade-answer: ## Grade a hand-written answer with the eval graders: make grade-answer CASE=billing-outstanding ANSWER="..." [NO_EVIDENCE=1]
	@$(COMPOSE) build -q evals >/dev/null
	@$(COMPOSE) run --rm --no-deps evals python -m evals.grade --case "$(CASE)" --answer "$(ANSWER)" $(if $(NO_EVIDENCE),--no-evidence)

load-evidence: ## Show the recorded live model matrix in the UI without credentials (copies docs/evidence -> evals/reports)
	@cp docs/evidence/2026-09-27-model-matrix/matrix-latest.json evals/reports/matrix-latest.json
	@echo "Recorded matrix loaded: open http://localhost:5173 -> Evals -> Model matrix (a later make eval-matrix overwrites it)"

eval-mutants: ## Mutation runs: prove each gate catches the regression it exists for
	$(COMPOSE) run --rm --no-deps evals python -m evals.run --mutants

# ---------------------------------------------------------------- quality gates
check: ## All quality gates: Python (in Docker) + UI
	$(COMPOSE) build -q evals >/dev/null
	docker build -q --target dev -t cfa-dev:local . >/dev/null
	docker run --rm cfa-dev:local sh -c "ruff check . && ruff format --check . && mypy && pytest -q -m 'not integration' && pip-audit --progress-spinner off -l --skip-editable"
	$(MAKE) ui-check

test: ## Python tests incl. integration tests against the running stack
	docker build -q --target dev -t cfa-dev:local . >/dev/null
	docker run --rm --network cfa_default --env-file .env \
	  -e EVAL_DATABASE_URL="postgresql://cfa_eval:$$(grep ^DB_PASSWORD_EVAL= .env | cut -d= -f2)@postgres:5432/cfa" \
	  -e IDP_URL=http://idp:8000 \
	  cfa-dev:local pytest -q

ui-check: ## UI: typecheck, lint, format, tests, audit
	cd ui && npm ci --no-audit --no-fund --silent && npm run check
