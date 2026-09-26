# ADR-004: One MCP server per bounded context, with skills inside it

- Status: accepted (supersedes the first cut, which ran three MCP servers)
- Date: 2026-09-26

## Context

The first version ran three MCP servers: `mcp-orders`, `mcp-billing` and `mcp-service`. Each had its own container, token audience and database role. That made isolation very visible. But orders, billing and service history all belong to **one system** (the customer-data / ERP-CRM bounded context), which has one owner, one release train and one SLO.

Three servers for one system would mean three deployments, three sets of certificates and alerts, and three audiences to register in the IdP and the MCP gateway. None of that buys anything when the same team ships all three together. It is not how we would run it in production.

What we **do** need to keep is what made the three-server version safe:
- entitlements per data product
- least-privilege data access
- lineage per data product
- a risk tier per data product, which drives promotion
- the ability to switch one data product off without touching the others

## Decision

**One MCP server per bounded context. Skills are the unit of governance inside it.**

`mcp-customer` (`src/cfa/skills/server.py`) is one process, one token audience and one endpoint. It hosts three skills. Each skill is a module (`src/cfa/skills/<skill>/`) with `manifest.yaml`, `skill.py`, `repository.py` and `models.py`.

| Concern | Unit | How |
|---|---|---|
| Deployment, scaling, transport, token audience | **Server** | One container, `aud=mcp-customer`, DNS-rebinding protection, one rate limiter per user |
| Entitlement | **Skill** | Each skill's `Boundary` checks its own agent scope and user role, plus the row tenant. The token's `scope` is down-scoped to the skill scopes the agent currently holds |
| Least privilege | **Skill** | One connection pool per skill, logging in as that skill's Postgres role (`cfa_orders`, `cfa_billing`, `cfa_service`) |
| Audit and lineage | **Skill** | `component = mcp-customer/<skill>` on every access decision |
| Risk tier and promotion | **Skill** | Declared in the skill manifest. The harness derives the composition's tier from the skills it binds, not from the server |
| Kill switch | **Skill** | `DISABLED_SKILLS=billing` denies that skill's tools (audited `skill_disabled`) while the other skills keep serving |
| Contract | **Skill** | The manifest declares the skill's tools. `create_server` refuses to start if a skill registers anything else |
| Discovery | **Skill** | Tools carry `_meta.skill` and a `[skill]` title. The `skills://catalog` resource lists the skills, their tools and the caller's access |

The agent still pins its tool names, descriptions and schemas client-side. The catalogue is for people (the UI Skills tab and `make skills`) and for governance, never for the model.

## Alternatives considered

1. **Keep three servers (one per data product).**
   - Pros: the strongest isolation, and a process-level blast radius.
   - Cons: triples the operational surface for no organisational benefit. It is the right shape only when the split criteria below apply.
2. **One server, with tool names namespaced as `billing.get_invoice`.**
   - Makes skills visible in the tool name, but changes the model-facing contract, the pinned tools and the eval cases for a purely cosmetic gain.
   - `_meta.skill` plus titles give the same grouping without touching the contract.
3. **Three servers aggregated behind an MCP gateway as one virtual server.**
   - This is the right pattern **across** bounded contexts, for example the customer server plus a future logistics server behind APIM or AgentCore Gateway.
   - Inside one system it simply adds the gateway on top of alternative 1.

## Consequences

**Gains:**
- One deployment.
- One audience to register.
- One MCP endpoint to put behind the gateway.
- Simpler compose, IdP and client code.
- Skills are easy to show and reason about.

**Costs:**
- **Shared blast radius.** A crash, memory leak or bad dependency in one skill affects all three.
  - Mitigations: the per-skill kill switch; per-skill DB roles, so a bug cannot read another skill's data; and one test suite that covers all three before the server ships.
- **Shared release cadence.** A billing fix redeploys orders too. Acceptable, because it is one team's system.
- **One audience means one token for all three skills.** The token is down-scoped to the agent's current scopes, and each skill checks its own scope and role, so holding the token does not grant billing. The eval harness and `test_user_without_role_is_denied` show this.

## When to split a skill into its own server

Split when **any** of these becomes true:
- **Different owner or release cadence.** Another team owns the data product.
- **Different data classification or network boundary.** For example, card data under PCI scope, or a separate VNet or private endpoint.
- **Divergent scaling or SLO.** One skill needs to scale or fail independently.
- **A different trust domain.** A third-party or partner system, or a different tenant model.

A split is mechanical:
1. Move the skill module into a new server that uses the same runtime (`create_server([MODULE])`).
2. Register a new audience with its scope in the IdP.
3. Point the agent's client for those tools at the new URL.

The manifests, audit components and eval cases do not change.

## Production

- **Azure:** the server runs in Container Apps behind the **APIM MCP gateway**. APIM can also apply per-tool policies keyed on tool name, which gives defence in depth on top of the in-server skill checks.
- **AWS:** the same pattern behind **AgentCore Gateway**.
- The skill catalogue maps onto the registry (API Center / the agent registry): one registered MCP server with per-skill metadata (tier, owner, scopes), which the promotion gate reads.
