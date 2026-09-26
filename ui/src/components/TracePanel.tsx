import { ms, time } from "../format";
import type { AuditEvent } from "../types";
import { Badge, type Tone } from "./Badge";

interface Props {
  traceId: string | null;
  events: AuditEvent[] | null;
  error: string | null;
}

const ACTION_LABEL: Record<AuditEvent["action"], string> = {
  route: "Route",
  model_call: "Model call",
  tool_call: "Tool call",
  access_decision: "Access decision",
  answer: "Answer",
};

function decisionTone(event: AuditEvent): Tone {
  if (event.decision === "allow") return "green";
  if (event.decision === "deny") return "red";
  if (event.decision === "error") return "amber";
  return "slate";
}

function summary(event: AuditEvent): string {
  switch (event.action) {
    case "route":
      return `intent "${event.decision ?? "?"}" → ${event.name ?? "?"} (classified by ${event.model_resolved ?? "?"})`;
    case "model_call":
      return `${event.model_alias ?? "?"} answered by ${event.model_resolved ?? "?"}${event.provider ? ` @ ${event.provider}` : ""}`;
    case "tool_call":
      return `${event.name ?? "?"}(${JSON.stringify(event.input.args ?? {})})`;
    case "access_decision":
      return `${event.component} · ${event.name ?? "?"} · ${event.reason ?? ""}`;
    case "answer":
      return `final answer by ${event.model_resolved ?? "?"}`;
  }
}

export function TracePanel({ traceId, events, error }: Props) {
  if (!traceId) {
    return (
      <Empty>
        Select an answer to see its lineage: routing decision, model calls, tool calls and the access decision
        taken at each data boundary.
      </Empty>
    );
  }
  if (error) return <Empty>{error}</Empty>;
  if (!events) return <Empty>Loading trace…</Empty>;

  const answer = events.find((e) => e.action === "answer");
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
        <p>
          <span className="font-medium text-slate-800">trace</span> <code>{traceId}</code>
        </p>
        <p>
          <span className="font-medium text-slate-800">user</span>{" "}
          {answer?.actor_user ?? events[0]?.actor_user} ·{" "}
          <span className="font-medium text-slate-800">acting agent</span>{" "}
          {answer?.actor_agent ?? events[0]?.actor_agent}
        </p>
      </div>
      <ol className="relative space-y-2 border-l border-slate-200 pl-4">
        {events.map((event) => (
          <li key={event.event_id} className="relative">
            <span className="absolute top-2 -left-[21px] h-2.5 w-2.5 rounded-full border-2 border-white bg-slate-300" />
            <details className="group rounded-lg border border-slate-200 bg-white p-2.5 open:shadow-sm">
              <summary className="flex cursor-pointer list-none flex-wrap items-center gap-1.5 text-xs">
                <span className="font-semibold text-slate-800">{ACTION_LABEL[event.action]}</span>
                <ComponentBadges component={event.component} />
                {event.decision && event.action !== "route" && (
                  <Badge tone={decisionTone(event)}>{event.decision}</Badge>
                )}
                <span className="ml-auto text-[11px] text-slate-400">
                  {ms(event.latency_ms)} {time(event.ts)}
                </span>
                <span className="basis-full text-slate-600">{summary(event)}</span>
              </summary>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px] text-slate-600">
                {event.policy_version && <Row label="policy">{event.policy_version}</Row>}
                {event.output_hash && <Row label="output hash">{event.output_hash.slice(0, 23)}…</Row>}
                {event.tokens_in !== null && (
                  <Row label="tokens">
                    {event.tokens_in} in / {event.tokens_out ?? 0} out
                  </Row>
                )}
              </dl>
              <pre className="mt-2 max-h-56 overflow-auto rounded bg-slate-900 p-2 text-[11px] leading-snug text-slate-100">
                {JSON.stringify({ input: event.input, output: event.output }, null, 2)}
              </pre>
            </details>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** ``mcp-customer/billing`` -> server badge + skill badge. */
function ComponentBadges({ component }: { component: string }) {
  const [server, skill] = component.split("/", 2);
  return (
    <>
      <Badge tone="slate">{server}</Badge>
      {skill && <Badge tone="indigo">skill: {skill}</Badge>}
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="font-medium text-slate-500">{label}</dt>
      <dd className="font-mono break-all">{children}</dd>
    </>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="p-2 text-sm text-slate-500">{children}</p>;
}
