# ADR-003: Deliberate stubs and simplifications

- Status: accepted
- Date: 2026-09-26

The brief asks for two deliberate stubs. Each is marked `STUB` in code, keeps a production-shaped interface, and is swappable without changing its callers.

## Stub 1: identity provider and token exchange (`src/cfa/identity/idp.py`, `/api/login`)

**What it does:**
- The mock IdP issues a user JWT for a chosen persona. There is no password: login is "pick a persona".
- It implements an **RFC 8693-shaped token exchange**. The agent presents its client secret plus the user token and receives a delegated token for the **skill server audience**, with claims:
  - `sub` = user
  - `act.sub` = `customer-assistant`
  - `aud` = `mcp-customer`
  - `scope` = the intersection of what the agent is registered for and the skill scopes that audience understands (`orders.read billing.read service.read`)
  - `customer_id`, `roles`

**What is real:**
- The token shape.
- Audience-bound, down-scoped tokens, so there is no token passthrough and no confused deputy.
- Signature and audience validation at the skill server, then each skill checks its own scope.
- Agent scopes read from `identity.agents`, which is how `make revoke-agent-scope` works.

**What is fake:**
- User authentication.
- Key management: signing keys are ephemeral and regenerated on IdP restart, so users must log in again.
- A shared client secret instead of a workload identity.

**Production:**
- **Azure:** Entra ID for the user (OIDC with PKCE in the SPA through the BFF), and **Entra Agent ID** for the agent, with on-behalf-of (OBO) flow producing per-API tokens.
- **AWS:** Cognito or the enterprise IdP, and **Bedrock AgentCore Identity**.

The skills only need a JWKS URL and an issuer to change.

## Stub 2: policy engine (`src/cfa/policy.py`)

**What it does:** `decide()` is a pure function. It allows a skill call only if all of these hold:
- the agent holds the scope
- the user holds the role
- the resource's tenant equals the token's `customer_id`

It returns a `Decision` carrying a reason and `POLICY_VERSION`, which is written to the audit.

**What is real:**
- The decision point sits **at the data boundary**, inside each skill, after the row is loaded. The tenant check therefore holds even if the LLM is talked into asking for another customer's data (eval case `deny-cross-tenant-*`, mutant M2).
- The decision is versioned and audited.

**What is fake:** the rules are Python, not a policy language, and there is no central PDP or policy distribution.

**Production:**
- OPA/Rego or Cedar as a PDP sidecar, or Amazon Verified Permissions (managed Cedar).
- Policies versioned in their own repo.
- The same `(principal, action, resource) → decision + reason + version` contract.

## Other simplifications (not stubs, but worth stating)

| Simplification | Why acceptable here | Production |
|---|---|---|
| `InMemorySaver` checkpointer | Conversation memory survives within an api process; demo threads are short | `PostgresSaver` / Redis checkpointer; checkpoint retention aligned with data policy |
| One container image for all Python services | One build, a fast demo; each service still runs as its own container with its own DB role | One image per service, signed, SBOM, independent release cadence |
| LiteLLM as the gateway | Runs on a laptop, and speaks the same OpenAI contract as APIM | APIM AI Gateway / Bedrock (ADR-001) |
| Fake scripted LLM | Evals and demo run with no credentials and deterministically | Live models; live evals use pass^3 |
| Audit in a Postgres table (append-only by grants) | Queryable from the UI and the graders | OTel GenAI spans to App Insights / CloudWatch, plus an immutable audit store (hash-chained / WORM) |
| Rate limit, WAF, content safety | Out of scope for a laptop slice | Gateway policies (APIM / Bedrock Guardrails) |
