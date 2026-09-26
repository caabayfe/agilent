# Customer Facing Assistant PoC. Run `make help` for targets.
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
PSQL := $(COMPOSE) exec -T postgres psql -U cfa_admin -d cfa -v ON_ERROR_STOP=1
PROFILE ?= fake
SCOPE ?= billing.read

.PHONY: help init up down reset logs ps gateway swap-provider unswap-provider break-provider fix-provider \
        skills disable-skill enable-skill break-billing fix-billing revoke-agent-scope restore-agent-scope eval eval-mutants check test ui-check

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

init: ## Create .env with fresh local secrets (model credentials left blank)
	@./scripts/init-env.sh
	@[[ -f gateway/active.yaml ]] || ./scripts/gateway.sh fake

up: init ## Build and start the whole stack (UI: http://localhost:5173)
	$(COMPOSE) up -d --build --wait
	@echo "UI  -> http://localhost:5173"
	@echo "API -> http://localhost:8000/healthz   (gateway profile: $$(cat gateway/active.profile))"

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

revoke-agent-scope: ## Revoke a scope from the agent identity: make revoke-agent-scope SCOPE=billing.read
	@$(PSQL) -c "UPDATE identity.agents SET scopes = array_remove(scopes, '$(SCOPE)') WHERE agent_id = 'customer-assistant';" >/dev/null
	@$(PSQL) -tAc "SELECT 'agent scopes now: ' || array_to_string(scopes, ', ') FROM identity.agents WHERE agent_id = 'customer-assistant';"

restore-agent-scope: ## Restore a revoked agent scope: make restore-agent-scope SCOPE=billing.read
	@$(PSQL) -c "UPDATE identity.agents SET scopes = array_append(scopes, '$(SCOPE)') WHERE agent_id = 'customer-assistant' AND NOT ('$(SCOPE)' = ANY(scopes));" >/dev/null
	@$(PSQL) -tAc "SELECT 'agent scopes now: ' || array_to_string(scopes, ', ') FROM identity.agents WHERE agent_id = 'customer-assistant';"

# ---------------------------------------------------------------- evals
eval: ## Baseline eval: gates, grader canaries, promotion decision
	$(COMPOSE) run --rm --no-deps evals python -m evals.run $(ARGS)

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
