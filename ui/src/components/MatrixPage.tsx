import { useEffect, useState } from "react";

import { api } from "../api";
import { classes } from "../format";
import type { MatrixCase, MatrixGate, MatrixResult, ModelMatrix } from "../types";
import { Badge } from "./Badge";

const ALIASES = ["assistant-fast", "assistant-reasoning"] as const;
const GATES = ["G1", "G2", "G3", "G4"] as const;

type Ok = Required<Omit<MatrixResult, "error" | "history">> & Pick<MatrixResult, "history">;

function isOk(r: MatrixResult): r is Ok {
  return r.error === undefined && r.cases !== undefined;
}

function entry<T>(record: Record<string, T> | undefined, key: string): T | undefined {
  return new Map(Object.entries(record ?? {})).get(key);
}

function fmtMs(ms: number | null | undefined): string {
  return ms == null ? "—" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${String(ms)} ms`;
}

function GateCell({ gate, latency = false }: { gate: MatrixGate | undefined; latency?: boolean }) {
  if (!gate) return <td className="px-2 py-2 text-center text-slate-300">—</td>;
  return (
    <td className="px-2 py-2 text-center" title={gate.detail}>
      <span
        className={classes(
          "inline-block min-w-14 rounded px-1.5 py-0.5 text-xs font-semibold",
          gate.passed ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800",
        )}
      >
        {gate.passed ? "PASS" : "FAIL"}{" "}
        {latency ? `${fmtMs(gate.score)} / ${fmtMs(gate.threshold)}` : gate.score.toFixed(2)}
      </span>
    </td>
  );
}

/** Full-page evidence: the same eval suite on every model combination. */
export function MatrixPage({ onBack }: { onBack: () => void }) {
  const [matrix, setMatrix] = useState<ModelMatrix | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<{ combo: string; caseId: string } | null>(null);

  const load = () => {
    api.matrix().then(
      (r) => {
        setMatrix(r.matrix);
        setError(null);
      },
      (e: unknown) => {
        setError(e instanceof Error ? e.message : "Could not load the matrix");
      },
    );
  };
  useEffect(load, []);

  const rows = (matrix?.results ?? []).filter(isOk);
  const failed = (matrix?.results ?? []).filter((r) => !isOk(r));
  const pickedRow = rows.find((r) => r.name === picked?.combo);
  const pickedCase: MatrixCase | undefined = picked ? entry(pickedRow?.cases, picked.caseId) : undefined;

  return (
    <main className="min-h-0 flex-1 overflow-y-auto bg-slate-50/60 p-6 text-sm">
      <div className="mx-auto max-w-7xl space-y-6">
        <div className="flex flex-wrap items-start gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Model matrix</h1>
            <p className="max-w-3xl text-xs text-slate-500">
              The same {matrix?.case_ids.length ?? ""} eval cases, the same agent code, prompt, routing
              policy, skills and deterministic graders in every column. Only the gateway changed: which model
              sits behind each alias. A single-model column puts that model behind <b>both</b> aliases, so it
              also does the router&apos;s intent classification.
            </p>
            {matrix && (
              <p className="mt-1 text-xs text-slate-500">
                Run {new Date(matrix.created_at).toLocaleString()} · pass^{matrix.repeats} (a case passes only
                if every repeat passes) · regenerate with{" "}
                <code className="rounded bg-slate-100 px-1">make eval-matrix</code>
                {!matrix.complete && (
                  <>
                    {" "}
                    · <Badge tone="amber">in progress / partial</Badge>
                  </>
                )}
              </p>
            )}
          </div>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={load}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              Refresh
            </button>
            <button
              type="button"
              onClick={onBack}
              className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
            >
              Back to assistant
            </button>
          </div>
        </div>

        {error && <p className="rounded bg-rose-50 p-2 text-xs text-rose-700">{error}</p>}
        {matrix === undefined && !error && <p className="text-slate-500">Loading…</p>}
        {matrix === null && (
          <p className="text-slate-500">
            No matrix yet. Run <code className="rounded bg-slate-100 px-1">make eval-matrix</code>.
          </p>
        )}

        {rows.length > 0 && matrix && (
          <>
            <section>
              <h2 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                Gates per combination
              </h2>
              <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 text-slate-500">
                    <tr>
                      <th className="px-2 py-2 text-left font-medium">Combination</th>
                      <th className="px-2 py-2 text-left font-medium">assistant-fast →</th>
                      <th className="px-2 py-2 text-left font-medium">assistant-reasoning →</th>
                      <th className="px-2 py-2 font-medium">Decision</th>
                      <th className="px-2 py-2 font-medium">G1 grounded</th>
                      <th className="px-2 py-2 font-medium">G2 leakage</th>
                      <th className="px-2 py-2 font-medium">G3 routing</th>
                      <th className="px-2 py-2 font-medium">G4 latency</th>
                      <th className="px-2 py-2 font-medium">Cases</th>
                      <th className="px-2 py-2 font-medium">p50 / p95</th>
                      <th className="px-2 py-2 font-medium">Tokens in / out</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const cases = Object.values(r.cases);
                      const lat = ALIASES.map((alias) => ({ alias, ...entry(r.aliases, alias) }));
                      return (
                        <tr key={r.name} className="border-t border-slate-100">
                          <td className="px-2 py-2 font-semibold text-slate-900">{r.name}</td>
                          {lat.map((a) => (
                            <td key={a.alias} className="px-2 py-2 font-mono text-slate-700">
                              {a.models?.join(", ") ?? "—"}
                              {a.refusals ? (
                                <span
                                  className="ml-1 font-sans text-[10px] text-slate-500"
                                  title="The deployment's content filter (Prompt Shields) blocked the request; the agent returned an audited refusal."
                                >
                                  ({a.refusals} refused by content filter)
                                </span>
                              ) : null}
                            </td>
                          ))}
                          <td className="px-2 py-2 text-center">
                            <Badge tone={r.decision === "certified" ? "green" : "amber"}>{r.decision}</Badge>
                          </td>
                          {GATES.map((g) => (
                            <GateCell key={g} gate={entry(r.gates, g)} latency={g === "G4"} />
                          ))}
                          <td className="px-2 py-2 text-center font-medium">
                            {cases.filter((c) => c.passed).length}/{cases.length}
                          </td>
                          <td className="px-2 py-2 text-center whitespace-nowrap text-slate-600">
                            {lat.map((a) => `${fmtMs(a.p50_ms)} / ${fmtMs(a.p95_ms)}`).join(" · ")}
                          </td>
                          <td className="px-2 py-2 text-center text-slate-600">
                            {r.tokens_in.toLocaleString()} / {r.tokens_out.toLocaleString()}
                          </td>
                        </tr>
                      );
                    })}
                    {failed.map((r) => (
                      <tr key={r.name} className="border-t border-slate-100">
                        <td className="px-2 py-2 font-semibold text-slate-900">{r.name}</td>
                        <td colSpan={10} className="px-2 py-2 text-rose-700">
                          Run failed: {r.error}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                G1–G4 are mandatory at risk tier 2. G4 holds each alias to its own p95 budget (fast 6 s,
                reasoning 12 s); the cell shows the alias closest to or over its budget. Latency is shown per
                alias (fast · reasoning).
              </p>
            </section>

            <section>
              <h2 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                Case by combination{" "}
                <span className="font-normal normal-case">(click a cell for the evidence)</span>
              </h2>
              <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 text-slate-500">
                    <tr>
                      <th className="px-2 py-2 text-left font-medium">Case</th>
                      {rows.map((r) => (
                        <th key={r.name} className="px-2 py-2 font-medium">
                          {r.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {matrix.case_ids.map((caseId) => (
                      <tr key={caseId} className="border-t border-slate-100">
                        <td className="px-2 py-1.5 font-mono text-slate-700">{caseId}</td>
                        {rows.map((r) => {
                          const c = entry(r.cases, caseId);
                          const active = picked?.combo === r.name && picked.caseId === caseId;
                          return (
                            <td key={r.name} className="px-1 py-1 text-center">
                              {c ? (
                                <button
                                  type="button"
                                  aria-label={`${caseId} on ${r.name}`}
                                  onClick={() => {
                                    setPicked({ combo: r.name, caseId });
                                  }}
                                  className={classes(
                                    "w-full rounded px-1.5 py-1 font-medium",
                                    c.passed
                                      ? "bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                                      : "bg-rose-100 text-rose-800 hover:bg-rose-200",
                                    active && "ring-2 ring-indigo-500",
                                  )}
                                >
                                  {c.passed ? "✓" : `✗ ${c.failed_gates.join("+")}`}{" "}
                                  <span className="font-normal opacity-60">
                                    {c.repeats_passed}/{c.repeats}
                                  </span>
                                </button>
                              ) : (
                                <span className="text-slate-300">—</span>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            {rows.some((r) => r.history?.length) && (
              <section>
                <h2 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                  Re-runs{" "}
                  <span className="font-normal normal-case">(earlier attempts are kept, not hidden)</span>
                </h2>
                <ul className="space-y-1 rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-700">
                  {rows.flatMap((r) =>
                    (r.history ?? []).map((h) => (
                      <li key={`${r.name}-${h.run_id ?? "error"}`}>
                        <span className="font-semibold">{r.name}</span> earlier run{" "}
                        <span className="font-mono">{h.run_id ?? "—"}</span>:{" "}
                        {h.decision ?? `error: ${h.error}`}
                        {Object.entries(h.failed_cases ?? {}).map(([cid, notes]) => (
                          <span key={cid} className="block pl-4 text-slate-500">
                            {cid}: {notes.join("; ")}
                          </span>
                        ))}
                      </li>
                    )),
                  )}
                </ul>
              </section>
            )}

            {picked && pickedCase && (
              <section className="rounded-lg border border-slate-200 bg-white p-4" aria-label="Case evidence">
                <div className="flex items-center gap-2">
                  <p className="font-semibold text-slate-900">
                    {picked.caseId} on {picked.combo}
                  </p>
                  <Badge tone={pickedCase.passed ? "green" : "red"}>
                    {pickedCase.passed ? "pass" : `fail ${pickedCase.failed_gates.join(", ")}`}
                  </Badge>
                  <span className="text-xs text-slate-500">
                    {pickedCase.repeats_passed}/{pickedCase.repeats} repeats clean
                  </span>
                </div>
                {pickedCase.notes.length > 0 && (
                  <>
                    <p className="mt-3 text-xs text-slate-500">Why the graders failed it:</p>
                    <ul className="list-disc pl-5 text-xs text-rose-800">
                      {pickedCase.notes.map((n) => (
                        <li key={n}>{n}</li>
                      ))}
                    </ul>
                  </>
                )}
                <p className="mt-3 text-xs text-slate-500">
                  {pickedCase.passed ? "Answer (first repeat)" : "Answer (first failing repeat)"}
                  {pickedCase.sample_model ? `, answered by ${pickedCase.sample_model}` : ""}:
                </p>
                <p className="rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap text-slate-800">
                  {pickedCase.sample_answer || "(empty)"}
                </p>
              </section>
            )}
          </>
        )}
      </div>
    </main>
  );
}
