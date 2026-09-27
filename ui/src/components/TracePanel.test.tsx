import { render, screen } from "@testing-library/react";

import type { AuditEvent } from "../types";
import { TracePanel } from "./TracePanel";

function event(overrides: Partial<AuditEvent>): AuditEvent {
  return {
    event_id: crypto.randomUUID(),
    ts: "2026-09-26T10:00:00Z",
    seq: 7,
    prev_hash: "sha256:" + "a".repeat(64),
    hash: "sha256:" + "b".repeat(64),
    trace_id: "t1",
    span_id: null,
    component: "customer-assistant",
    action: "tool_call",
    name: null,
    actor_user: "bob",
    actor_agent: "customer-assistant",
    decision: null,
    reason: null,
    model_alias: null,
    model_resolved: null,
    provider: null,
    input: {},
    output: {},
    output_hash: null,
    latency_ms: null,
    tokens_in: null,
    tokens_out: null,
    policy_version: null,
    ...overrides,
  };
}

describe("TracePanel", () => {
  it("shows a deny decision at the data boundary with its reason", () => {
    render(
      <TracePanel
        traceId="t1"
        error={null}
        events={[
          event({
            action: "access_decision",
            component: "mcp-customer/billing",
            name: "list_invoices",
            decision: "deny",
            reason: "user_role_missing",
            policy_version: "entitlements-2026-09-26.1",
          }),
        ]}
      />,
    );
    expect(screen.getByText("deny")).toBeInTheDocument();
    expect(screen.getByText(/mcp-customer\/billing · list_invoices · user_role_missing/)).toBeInTheDocument();
  });

  it("shows which alias was requested and which model answered", () => {
    render(
      <TracePanel
        traceId="t1"
        error={null}
        events={[
          event({
            action: "model_call",
            model_alias: "assistant-reasoning",
            model_resolved: "openai/fake-fast (failover)",
          }),
        ]}
      />,
    );
    expect(
      screen.getByText(/assistant-reasoning answered by openai\/fake-fast \(failover\)/),
    ).toBeInTheDocument();
  });

  it("links the trace and each audit event to its OpenTelemetry span", () => {
    render(
      <TracePanel
        traceId="0af7651916cd43dd8448eb211c80319c"
        error={null}
        events={[event({ action: "answer", span_id: "b7ad6b7169203331" })]}
      />,
    );
    expect(screen.getByRole("link", { name: /Open spans in Jaeger/ })).toHaveAttribute(
      "href",
      "http://localhost:16686/trace/0af7651916cd43dd8448eb211c80319c",
    );
    expect(screen.getByRole("link", { name: /b7ad6b7169203331/ })).toHaveAttribute(
      "href",
      "http://localhost:16686/trace/0af7651916cd43dd8448eb211c80319c?uiFind=b7ad6b7169203331",
    );
    expect(screen.getByText(/#7 · sha256:bbbbbbbbbbbb/)).toBeInTheDocument();
  });

  it("renders model output as text, not HTML", () => {
    render(
      <TracePanel
        traceId="t1"
        error={null}
        events={[event({ action: "answer", output: { answer: "<img src=x onerror=alert(1)>" } })]}
      />,
    );
    expect(document.querySelector("img")).toBeNull();
  });
});
