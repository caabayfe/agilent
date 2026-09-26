# ADR-002: No A2A in this slice

- Status: accepted
- Date: 2026-09-26

## Context

A2A (Agent-to-Agent) is a protocol for one *agent* to delegate a task to another, independently owned and deployed, *agent*. It covers discovery (agent cards), task lifecycle, streaming, and long-running work. MCP is a protocol for an agent to call *tools and data* (skills).

The Customer Facing Assistant needs three things: read orders, read billing, read service history and knowledge base. Each is a deterministic data product with a clear schema and an owner.

## Decision

The skills are **MCP servers, not agents**, and there is no A2A in this slice.

- **Nothing here reasons on its own behalf.** A skill that runs a SQL query does not need a task lifecycle, negotiation or its own model. Making it an agent would add a second LLM hop (latency, cost, a new failure mode) and a second place where prompt injection can land.
- **Identity is harder across A2A, not easier.** In this slice the delegated token is `sub = user`, `act = agent`, `aud = one skill`, and every skill enforces the tenant rule. An A2A hop would add a second actor to the chain (`act.act`). It would also need a policy for what the downstream agent may do with the user's authority. That problem has no settled answer yet, and it is not needed to answer "where is my order?".
- **The eval surface stays closed.** Gates G1–G3 grade tool calls and access decisions recorded in one audit trace. With a remote agent in the loop, the thing being certified includes someone else's prompt and model.

## When A2A becomes warranted

Any one of these would change the decision:

1. A second team owns an **agent**, not a data product. Example: a "Returns & RMA agent" that runs a multi-step, human-in-the-loop workflow with its own state and SLAs.
2. The work is **long-running or asynchronous**, lasting hours or days, such as a field-service dispatch. MCP's request/response shape then becomes a poor fit.
3. The capability must be reached from **outside the platform**, for example by a partner's agent, where an agent card and a task contract are the right interface.

## How we would add it

- Expose it behind the same gateway control point. APIM already imports and governs A2A agent APIs.
- Carry the delegation chain (RFC 8693 `act` nesting) and scope-down at each hop.
- Treat the remote agent as a **dependency with its own certificate**. Our composition's promotion binds to the remote agent's certified version, just as it binds to `gateway_config_hash` today.
