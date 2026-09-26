import type { Skill, SkillCatalog } from "../types";
import { Badge } from "./Badge";
import { Empty } from "./TracePanel";

interface Props {
  catalog: SkillCatalog | null;
  error: string | null;
  persona: string | undefined;
  busy: boolean;
  onAsk: (question: string) => void;
}

const REASON = new Map<string, string>([
  ["user_role_missing", "user lacks role"],
  ["agent_scope_missing", "agent lacks scope"],
  ["skill_disabled", "switched off"],
]);

export function SkillsPanel({ catalog, error, persona, busy, onAsk }: Props) {
  if (error) return <Empty>{error}</Empty>;
  if (!catalog) return <Empty>Loading skills…</Empty>;
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
        <p>
          <span className="font-medium text-slate-800">1 MCP server</span> <code>{catalog.server}</code> ·{" "}
          <span className="font-medium text-slate-800">{catalog.skills.length} skills</span> · as seen by{" "}
          <span className="font-medium text-slate-800">{persona}</span>
        </p>
        <p className="mt-1">
          The server is the deployment unit. Each skill keeps its own entitlement (agent scope + user role),
          database role, risk tier and audit identity.
        </p>
      </div>
      {catalog.skills.map((skill) => (
        <SkillCard key={skill.name} skill={skill} busy={busy} onAsk={onAsk} />
      ))}
    </div>
  );
}

function SkillCard({ skill, busy, onAsk }: { skill: Skill; busy: boolean; onAsk: (q: string) => void }) {
  const { allowed, reason } = skill.access;
  return (
    <section
      aria-label={`${skill.name} skill`}
      className="rounded-lg border border-slate-200 bg-white p-3 text-xs shadow-sm"
    >
      <header className="flex flex-wrap items-center gap-1.5">
        <h3 className="text-sm font-semibold text-slate-900">{skill.title}</h3>
        <Badge tone="indigo">{skill.name}</Badge>
        <Badge tone={skill.risk_tier >= 2 ? "amber" : "slate"}>tier {skill.risk_tier}</Badge>
        <span className="ml-auto">
          <Badge tone={allowed ? "green" : "red"}>
            {allowed ? "allowed" : `denied · ${REASON.get(reason) ?? reason}`}
          </Badge>
        </span>
      </header>
      <p className="mt-1 text-slate-600">{skill.description}</p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px] text-slate-600">
        <dt className="font-medium text-slate-500">agent scope</dt>
        <dd className="font-mono">{skill.required_agent_scope}</dd>
        <dt className="font-medium text-slate-500">user role</dt>
        <dd className="font-mono">{skill.required_user_role}</dd>
        <dt className="font-medium text-slate-500">audit as</dt>
        <dd className="font-mono">{skill.component}</dd>
      </dl>
      <ul className="mt-2 space-y-1">
        {skill.tools.map((tool) => (
          <li key={tool.name} className="rounded bg-slate-50 px-2 py-1">
            <code className="font-semibold text-slate-800">
              {tool.name}({Object.keys(tool.input_schema.properties ?? {}).join(", ")})
            </code>
            <span className="block text-slate-500">{tool.description?.split("\n")[0]}</span>
          </li>
        ))}
      </ul>
      {skill.examples.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {skill.examples.map((q) => (
            <button
              key={q}
              type="button"
              disabled={busy}
              onClick={() => {
                onAsk(q);
              }}
              className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] text-slate-700 hover:border-indigo-300 hover:bg-indigo-50 disabled:opacity-50"
            >
              Ask: {q}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
