# ADR-001: Model gateway and routing

- Status: accepted for the PoC; the production direction is proposed
- Date: 2026-09-26

## Context

The brief requires that a provider swap be "a mechanical change at the gateway", that the agent survive it, and that we can say which model answered each request. The PoC also has to show a model router across two models: gpt-5-mini (fast) and gpt-5.4 (reasoning).

"Model routing" hides two different decisions:

1. **Which class of model this request deserves.** This is a business and risk decision: troubleshooting needs reasoning, while a billing lookup needs a cheap, pinned, evaluated model.
2. **Which vendor deployment serves that class right now.** This is an operational decision covering keys, quota, region, failover and price.

They have different owners and change at different rates. If they are mixed together, a provider swap becomes an application change.

## Decision

Routing is split into three layers. Only layers 1 and 2 are required.

| Layer | Decides | Owner | PoC | Azure production | AWS production |
|---|---|---|---|---|---|
| 1. Routing policy | intent + risk → **alias** (`assistant-fast`, `assistant-reasoning`) | Agent plane (versioned, evaluated) | `route` node + `choose_alias()` in `src/cfa/agent/routing.py` | Same code, shipped as a shared harness module | Same |
| 2. Model gateway | alias → deployment; keys, quotas, failover, telemetry, content safety | Platform team | LiteLLM container, config in `gateway/profiles/*.yaml` | **Azure API Management AI Gateway**: backend pools and circuit breaker, token-limit and token-metric policies, managed identity to backends, semantic cache, content safety | **Bedrock** (Converse API, cross-region inference profiles, Guardrails). LiteLLM or Portkey on EKS for vendors outside Bedrock |
| 3. Managed router (optional) | per-prompt pick inside one vendor's catalogue | Vendor | not used | **Foundry Model Router** deployment | **Bedrock Intelligent Prompt Routing** |

### Rules

1. **The agent only knows aliases.** No model name, endpoint or key exists in the agent code or its config. `make swap-provider` changes only `gateway/active.yaml`. The agent is not restarted, and the audit shows the new `model_resolved`.
2. **Layer 1 stays explicit, deterministic and in-app.** It encodes rules a managed router cannot express. Examples:
   - "billing (a tier-2 financial data product) uses a pinned model"
   - "cross-tenant-sensitive flows never go to an auto-router"

   It is a lookup table with a version (`ROUTING_POLICY_VERSION`), so eval gate G3 can test it and promotion binds to it.
3. **Failover belongs to the gateway, not the app.** In the PoC this is `router_settings.fallbacks`; in production, APIM backend pools with a circuit breaker. `make break-provider` shows this: the answer still arrives, and the audit records `model_resolved = ... (failover)`.
4. **A managed router (layer 3) may only sit *behind* an alias, never act as the seam.** Conditions:
   - tier-1 compositions only
   - pinned router version and model subset
   - `model_resolved` logged (both services return the served model)
   - a router version change counts as a promotion event, so the eval gates re-run and the certificate is marked stale

   *Why it cannot be the seam:*
   - Its policy is vendor-owned and trained, not declared.
   - Its model set is one catalogue: Foundry's own models (Claude must be deployed separately), or Bedrock IPR within **one model family**.
   - This breaks vendor neutrality, "why did this model answer" lineage, and eval reproducibility.

### Why APIM AI Gateway for production (Azure estate)

- It fronts Foundry, Bedrock, Vertex and self-hosted models behind one OpenAI-compatible contract. It has a *unified model API* (preview), so vendor neutrality is enforced by the platform, not by each agent.
- It already governs MCP servers and A2A agent APIs. The same control point can later hold the agent → skill hop (see `production-mapping.md`).
- It uses managed identity to backends, so no model keys live in agent pods. The PoC's version of this is "only the gateway reads `MODEL_*` from `.env`".
- The existing reference estate (`agentic-service`) already uses APIM subscription keys.

On an AWS-only estate, Bedrock covers most vendors natively. LiteLLM on EKS remains the fallback gateway for anything outside it.

## Consequences

- **Good:**
  - Provider swaps are config diffs.
  - The routing policy is testable and part of the promotion binding (`routing_policy`, `gateway_config_hash`).
  - Failover and quota are handled in one place.
- **Cost:**
  - One extra network hop and one more component to run.
  - The alias vocabulary must be governed: adding an alias is a platform change.
- **Things that had to change beyond config when adding gpt-5.x:** gpt-5.x rejects non-default `temperature`. The gateway strips it (`drop_params: true`), so no agent change was needed, but it has to be listed as a model-contract difference in the gateway change record.

## What would change this decision

- A single mandated hyperscaler: the managed gateway alone is enough and LiteLLM is dropped entirely.
- Managed routers gaining customer-declared policies, pinning and explanations. Layer 3 could then absorb part of layer 1 for tier-1 workloads.

## Sources (checked 2026-09-26)

- Model router for Microsoft Foundry: https://learn.microsoft.com/azure/foundry/openai/concepts/model-router
- AI gateway capabilities in Azure API Management: https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities
- Amazon Bedrock intelligent prompt routing: https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html
- LiteLLM router and fallbacks: https://docs.litellm.ai/docs/routing
