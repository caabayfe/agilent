import { classes } from "../format";
import type { EvalsView } from "../types";
import { Badge } from "./Badge";
import { Empty } from "./TracePanel";

const DECISION_STYLE = {
  certified: "border-emerald-300 bg-emerald-50 text-emerald-900",
  blocked: "border-amber-300 bg-amber-50 text-amber-900",
  harness_invalid: "border-rose-300 bg-rose-50 text-rose-900",
} as const;

const GATES = ["G1", "G2", "G3", "G4"] as const;

export function EvalsPanel({ view, onRefresh }: { view: EvalsView | null; onRefresh: () => void }) {
  if (!view) return <Empty>Loading eval results…</Empty>;
  const { baseline, mutants, staleness } = view;
  if (!baseline) {
    return (
      <Empty>
        No eval run yet. Run <code className="rounded bg-slate-100 px-1">make eval</code> and{" "}
        <code className="rounded bg-slate-100 px-1">make eval-mutants</code>.
      </Empty>
    );
  }
  const d = baseline.decision;
  return (
    <div className="space-y-4 text-sm">
      <div className={classes("rounded-lg border p-3", DECISION_STYLE[d.decision])}>
        <div className="flex items-center gap-2">
          <p className="text-xs font-semibold tracking-wide uppercase opacity-70">Promotion decision</p>
          {staleness.stale && <Badge tone="red">stale</Badge>}
          <button
            type="button"
            onClick={onRefresh}
            className="ml-auto text-xs underline opacity-70 hover:opacity-100"
          >
            refresh
          </button>
        </div>
        <p className="mt-1 text-xl font-bold">{d.decision.replace("_", " ").toUpperCase()}</p>
        <p className="text-xs opacity-80">
          risk tier {d.risk_tier} (derived:{" "}
          {Object.entries(d.tier_sources)
            .map(([k, v]) => `${k}=${v}`)
            .join(", ")}
          ) · mandatory {d.mandatory.join(", ")} · {baseline.mode} mode, pass^{baseline.repeats}
        </p>
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs">
          {d.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        {staleness.stale && (
          <p className="mt-2 rounded bg-white/60 p-2 text-xs">
            Certificate no longer matches what is deployed (changed: <b>{staleness.changed.join(", ")}</b>).
            Re-run <code>make eval</code>.
          </p>
        )}
      </div>

      <section>
        <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-slate-500 uppercase">Gates</h3>
        <ul className="space-y-1.5">
          {Object.values(baseline.gates).map((g) => (
            <li key={g.id} className="rounded-lg border border-slate-200 bg-white p-2.5">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-800">
                  {g.id} {g.name}
                </span>
                <Badge tone={g.passed ? "green" : "red"}>{g.passed ? "pass" : "fail"}</Badge>
                {d.advisory.includes(g.id) && <Badge>advisory</Badge>}
              </div>
              <p className="text-xs text-slate-500">Catches: {g.catches}</p>
              <p className="text-xs text-slate-700">{g.detail}</p>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-slate-500 uppercase">
          Grader canaries: plausible-looking wrong answers{" "}
          <Badge tone={baseline.canaries.ok ? "green" : "red"}>
            {baseline.canaries.results.filter((c) => c.ok).length}/{baseline.canaries.results.length} rejected
          </Badge>
        </h3>
        <ul className="space-y-1.5">
          {baseline.canaries.results.map((c) => (
            <li key={c.id}>
              <details className="rounded-lg border border-slate-200 bg-white p-2.5 text-xs">
                <summary className="flex cursor-pointer list-none items-center gap-2">
                  <Badge tone={c.ok ? "green" : "red"}>{c.ok ? "rejected" : "ACCEPTED"}</Badge>
                  <span className="text-slate-700">{c.corruption}</span>
                </summary>
                <p className="mt-2 text-slate-500">Doctored answer:</p>
                <p className="rounded bg-slate-50 p-1.5 text-slate-800 italic">{c.doctored}</p>
                <p className="mt-1 text-slate-500">Why the graders rejected it:</p>
                <ul className="list-disc pl-4 text-slate-700">
                  {c.rejected_because.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </details>
            </li>
          ))}
        </ul>
      </section>

      {mutants && (
        <section>
          <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Mutation runs: does each gate catch its regression?{" "}
            <Badge tone={mutants.all_killed ? "green" : "red"}>
              {mutants.all_killed ? "all killed" : "survivors"}
            </Badge>
          </h3>
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-2 py-1.5 text-left font-medium">Mutant</th>
                  {GATES.map((g) => (
                    <th key={g} className="px-2 py-1.5 font-medium">
                      {g}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {mutants.rows.map((row) => (
                  <tr key={row.id} className="border-t border-slate-100">
                    <td className="px-2 py-1.5">
                      <p className="font-medium text-slate-800">
                        {row.id === "baseline" ? "baseline" : `${row.id} ${row.name}`}
                      </p>
                      <p className="text-slate-500">
                        {row.regression === "none" ? "no regression" : row.regression}
                      </p>
                    </td>
                    {GATES.map((g) => {
                      const caught = new Map(Object.entries(row.gates)).get(g)?.passed === false;
                      const target = row.target_gate === g;
                      return (
                        <td key={g} className="px-2 py-1.5 text-center">
                          <span
                            className={classes(
                              "inline-block min-w-12 rounded px-1 py-0.5",
                              caught && target && "bg-emerald-600 font-semibold text-white",
                              caught && !target && "bg-slate-200 text-slate-700",
                              !caught && target && "bg-rose-600 font-semibold text-white",
                              !caught && !target && "text-slate-300",
                            )}
                          >
                            {caught ? "caught" : target ? "MISSED" : "·"}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-1 text-[11px] text-slate-500">
            Green = caught by the gate that exists for that failure. The baseline row shows G4 failing on
            purpose.
          </p>
        </section>
      )}

      <section>
        <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-slate-500 uppercase">
          Certificate bound to
        </h3>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded-lg border border-slate-200 bg-white p-2.5 text-[11px]">
          {Object.entries(baseline.bindings).map(([k, v]) => (
            <div key={k} className="contents">
              <dt
                className={classes(
                  "text-slate-500",
                  staleness.changed.includes(k) && "font-semibold text-rose-600",
                )}
              >
                {k}
              </dt>
              <dd className="font-mono break-all text-slate-700">{v}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
