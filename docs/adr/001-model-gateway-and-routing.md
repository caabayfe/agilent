# ADR-001: Model gateway and routing

- Status: accepted for the PoC; the production direction is proposed
- Date: 2026-09-26

## Context

The brief requires that a provider swap be "a mechanical change at the gateway", that the agent survive it, and that we can say which model answered each request. The PoC also has to show a model router across two models: a fast model and gpt-5.4 (reasoning). gpt-5-mini was planned for the fast alias; the available Azure resource has no gpt-5-mini deployment, so the live profile uses gpt-4.1-mini. That substitution was a `.env` change only, which is the point of the alias seam.

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

1. **The agent only knows aliases.** No model name, endpoint or key exists in the agent code or its config. `make swap-provider` changes only `gateway/active.yaml`. The agent is not restarted, and the audit shows the new `model_resolved`. `make compare-providers` runs the same evals on both sides and reports whether behaviour changed too.
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
- **Things that had to change beyond config:** measured with `make compare-providers`, not asserted. See "Provider swap: what did and did not change" below.

## Provider swap: what did and did not change

**Plainly:** in the PoC, "two providers" means two Azure OpenAI deployments (gpt-4.1-mini and gpt-5.4): two models, one vendor, one API contract. That is the weakest form of the claim. A cross-vendor slot exists (`gateway/profiles/live-alt.yaml`, `MODEL_ALT_*`, e.g. a Foundry catalogue model such as DeepSeek or Llama via `azure_ai/`). It has **not been run**, because the resource has no non-OpenAI deployment. `gateway/model-contracts.yaml` lists what we expect to break there: strict `json_schema` structured output for the router.

What `make swap-provider` changed, and what the same 20 eval cases (pass^3) then showed:

| | Did it change? |
|---|---|
| Agent code, routing policy, skill manifests, graph | No for the swap itself: hash-identical bindings, agent not restarted |
| Gateway config | Yes: 14 lines in the profile (alias → deployment) |
| **Behaviour** | **Yes.** 4 of 20 cases regressed on the swapped side |

What we then had to change to make the swapped side pass G1–G3:

1. **Gateway config.** `content_policy_fallbacks` in every profile. gpt-5.4's deployment blocks jailbreak prompts (Azure Prompt Shields). LiteLLM treated the block as an outage and failed it over to gpt-4.1-mini, whose deployment has no jailbreak filter, and that model answered. A safety refusal must never be failed over.
2. **Agent code.** This is a change beyond configuration. `src/cfa/agent/refusals.py`, used by the router node and `AuditMiddleware`, turns a provider block into an audited refusal instead of a crashed run. It keys on the gateway's normalised `ContentPolicyViolationError`, not on a vendor's error code, so it stays vendor-neutral. We would have needed this for any provider with a blocking safety layer. The swap is what exposed it.
3. **Prompt.** This is also a change beyond configuration. `system.md` rule 1 now forbids computing new totals: gpt-5.4 summed two invoices, and G1 rejects that as ungrounded. Rule 6 now requires the answer to start with the KB id: gpt-4.1-mini dropped the citation. The new prompt was re-evaluated on **both** providers.

Items 2 and 3 are one-off harness fixes, now bound into the certificate (`agent_code_hash`, `prompt_hash`). A future swap between these two deployments is back to config-only, and the comparison proves it. G4 (latency) failed on both sides under the original single 4 s budget. It now has a budget per model class (6 s fast, 12 s reasoning), and both sides pass. The gates, not the swap, decide promotion.

### Operating the swap from the UI

The header's model selector re-points each alias at a model from `gateway/catalog.yaml` (the `.env` deployments, other deployments on the same Azure resource, the offline fakes, the optional `MODEL_ALT_*` vendor). It is the same mechanism as `make gateway`, not a new one: `cfa.gateway_admin` supervises LiteLLM inside its container, writes `gateway/active.yaml`, restarts the gateway, and rolls back if it does not come up healthy. The BFF proxies to it (`/api/gateway/catalog`, `PUT /api/gateway/selection`) with the gateway master key; the browser never sees a key or a base URL beyond its host.

- The agent is still untouched: no restart, no code, no prompt change. It keeps calling `assistant-fast` / `assistant-reasoning`.
- A selection that equals a named profile is written byte-for-byte as that profile, so its certificate still applies. Anything else is profile `custom`: `gateway_config_hash` changes and the certificate goes **STALE**. Picking a model in a dropdown is exactly the kind of swap the table above shows can change behaviour, so it is a promotion event, not a free action.
- Local demo only: there is no operator role in the PoC, so any signed-in persona can use it (`GATEWAY_ADMIN_ENABLED`, on in `docker-compose.yml`, off by default in the settings). In production this is a change to the gateway's config repo, reviewed and followed by the eval run, not a button.

### Every model in the selector (`make eval-matrix`)

The same suite, with nothing but the gateway changed, was run on every usable model in the catalogue. Per-model differences it found are recorded in `gateway/model-contracts.yaml`, each with where it is absorbed:
- gpt-5.4 intermittently puts an order id into the E-217 troubleshooting steps. It blocked `live` in the matrix although the certificate run of the same config had passed.
- o4-mini writes "EUR 9 120.00". Not absorbed: it needs a prompt or grader change.
- Reasoning models on the fast path (gpt-5.4 swapped, gpt-5, o4-mini) exceed the 6 s lookup budget.
- gpt-4.1 once returned router output that failed validation. It did not reproduce.
- Prompt Shields is set per deployment.

Result: 4 of 8 combinations certified. It is not a model ranking: the prompt was tuned on the same cases, and G1 grades facts, not advice (`docs/evals.md`, "Why nearly every model passes").

## What would change this decision

- A single mandated hyperscaler: the managed gateway alone is enough and LiteLLM is dropped entirely.
- Managed routers gaining customer-declared policies, pinning and explanations. Layer 3 could then absorb part of layer 1 for tier-1 workloads.

## Sources (checked 2026-09-26)

- Model router for Microsoft Foundry: https://learn.microsoft.com/azure/foundry/openai/concepts/model-router
- AI gateway capabilities in Azure API Management: https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities
- Amazon Bedrock intelligent prompt routing: https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html
- LiteLLM router and fallbacks: https://docs.litellm.ai/docs/routing
