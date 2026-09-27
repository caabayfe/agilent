"""Provider swap: what had to change, and did behaviour change?

    python -m evals.compare <profile-a> <profile-b>

Reads two labelled baselines (``python -m evals.run --label <profile>``, run once per
gateway profile; ``make compare-providers`` does all of it) and answers the brief's
question about a model swap, in three parts:

1. **What changed to make the swap.** The gateway profile diff, and every other
   certificate binding (prompt, routing policy, skill manifests, dataset, graph
   version). If any of those differ, something beyond configuration changed, and the
   report says so.
2. **What the swap did to behaviour.** Same cases, same graders, both providers: gate
   by gate, case by case (verdicts, tool plan, ReAct loop length, tokens, latency),
   with both answers side by side where behaviour diverged.
3. **What had to be adapted for each model.** The declared model-contract ledger
   (``gateway/model-contracts.yaml``) for the models that actually answered, stating
   where each difference is absorbed: gateway config, prompt, or agent code.

The swap being mechanical does not make it promotable: that is still the gates' call.
"""

import argparse
import difflib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from evals.run import EvalSettings

GATES = ("G1", "G2", "G3", "G4")
CASE_GATES = ("G1", "G2", "G3")
AGENT_BINDINGS = (
    "graph_version",
    "agent_code_hash",
    "prompt_hash",
    "routing_policy",
    "skill_manifests_hash",
    "dataset_hash",
)
ANSWER_CHARS = 420


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"missing {path}: run `python -m evals.run --label {path.stem}` on that profile first")
    report: dict[str, Any] = json.loads(path.read_text())
    return report


def resolved_models(report: dict[str, Any]) -> dict[str, dict[str, int]]:
    """alias -> {model that actually answered: runs}."""
    out: dict[str, Counter[str]] = {}
    for case in report["cases"]:
        for run in case["runs"]:
            out.setdefault(run["alias"] or "unknown", Counter())[run["model_resolved"] or "unknown"] += 1
    return {alias: dict(counter.most_common()) for alias, counter in sorted(out.items())}


def config_diff(profiles_dir: Path, a: str, b: str) -> list[str]:
    left = (profiles_dir / f"{a}.yaml").read_text().splitlines()
    right = (profiles_dir / f"{b}.yaml").read_text().splitlines()
    return [
        line
        for line in difflib.unified_diff(left, right, lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]


def _dominant(values: list[str]) -> str:
    return Counter(values).most_common(1)[0][0] if values else "-"


def case_profile(case: dict[str, Any]) -> dict[str, Any]:
    runs = case["runs"]
    notes = sorted(
        {
            f"{gate}: {note}"
            for run in runs
            for gate, verdict in run["verdicts"].items()
            if not verdict["passed"]
            for note in verdict.get("notes", [])
        }
    )
    return {
        "gates": case["gates"],
        "tools": _dominant([" > ".join(run["tools"]) or "(none)" for run in runs]),
        "alias": _dominant([run["alias"] or "unknown" for run in runs]),
        "model": _dominant([run["model_resolved"] or "unknown" for run in runs]),
        "latency_p50_ms": int(statistics.median(run["latency_ms"] for run in runs)) if runs else 0,
        "model_calls": round(statistics.mean(run.get("model_calls", 0) for run in runs), 1) if runs else 0,
        "tokens_out": int(statistics.mean(run.get("tokens_out", 0) for run in runs)) if runs else 0,
        "answer": runs[0]["answer"] if runs else "",
        "failure_notes": notes,
    }


def diff_cases(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    b_by_id = {c["id"]: c for c in b["cases"]}
    rows = []
    for case_a in a["cases"]:
        case_b = b_by_id.get(case_a["id"])
        if case_b is None:
            continue
        pa, pb = case_profile(case_a), case_profile(case_b)
        regressed = [g for g in CASE_GATES if pa["gates"][g] is True and pb["gates"][g] is False]
        improved = [g for g in CASE_GATES if pa["gates"][g] is False and pb["gates"][g] is True]
        changes = []
        if regressed:
            changes.append("regressed " + ",".join(regressed))
        if improved:
            changes.append("improved " + ",".join(improved))
        if pa["tools"] != pb["tools"]:
            changes.append("tool plan")
        if pa["alias"] != pb["alias"]:
            changes.append("routing")
        if abs(pa["model_calls"] - pb["model_calls"]) >= 1:
            changes.append("loop length")
        rows.append(
            {
                "id": case_a["id"],
                "question": case_a["question"],
                "risk": case_a["risk"],
                "a": pa,
                "b": pb,
                "regressed": regressed,
                "improved": improved,
                "changes": changes,
            }
        )
    return rows


def contract_notes(ledger_path: Path, models: set[str]) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        return []
    ledger: list[dict[str, Any]] = yaml.safe_load(ledger_path.read_text()).get("models", [])
    hits = []
    for entry in ledger:
        matched = sorted(m for m in models if any(p in m for p in entry["match"]))
        if matched:
            hits.append(entry | {"matched": matched})
    return hits


def compare(a: dict[str, Any], b: dict[str, Any], profiles_dir: Path, ledger: Path) -> dict[str, Any]:
    la, lb = a["mode"], b["mode"]
    models_a, models_b = resolved_models(a), resolved_models(b)
    beyond_config = [
        {"binding": k, "a": a["bindings"].get(k), "b": b["bindings"].get(k)}
        for k in AGENT_BINDINGS
        if a["bindings"].get(k) != b["bindings"].get(k)
    ]
    cases = diff_cases(a, b)
    all_models = {m for per_alias in (*models_a.values(), *models_b.values()) for m in per_alias}
    return {
        "a": {"profile": la, "run_id": a["run_id"], "repeats": a["repeats"], "models": models_a},
        "b": {"profile": lb, "run_id": b["run_id"], "repeats": b["repeats"], "models": models_b},
        "config_diff": config_diff(profiles_dir, la, lb),
        "gateway_config_hash": {"a": a["bindings"]["gateway_config_hash"], "b": b["bindings"]["gateway_config_hash"]},
        "beyond_config": beyond_config,
        "gates": {g: {"a": a["gates"][g], "b": b["gates"][g]} for g in GATES},
        "decision": {"a": a["decision"], "b": b["decision"]},
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "identical": sum(1 for c in cases if not c["changes"]),
            "regressed": [c["id"] for c in cases if c["regressed"]],
            "improved": [c["id"] for c in cases if c["improved"]],
            "tool_plan_changed": [c["id"] for c in cases if "tool plan" in c["changes"]],
            "tokens_out": {
                "a": sum(c["a"]["tokens_out"] for c in cases),
                "b": sum(c["b"]["tokens_out"] for c in cases),
            },
        },
        "contracts": contract_notes(ledger, all_models),
    }


# ------------------------------------------------------------------ rendering
def _fmt_models(models: dict[str, dict[str, int]]) -> str:
    return "; ".join(f"`{alias}` → " + ", ".join(f"{m} ({n})" for m, n in per.items()) for alias, per in models.items())


def _gate_cell(gate: dict[str, Any]) -> str:
    return f"{'PASS' if gate['passed'] else 'FAIL'} {gate['score']}"


def _clip(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= ANSWER_CHARS else flat[:ANSWER_CHARS] + " …"


def render(report: dict[str, Any]) -> str:
    a, b, s = report["a"], report["b"], report["summary"]
    header = [
        f"# Provider swap: `{a['profile']}` → `{b['profile']}`",
        "",
        f"Same {s['cases']} cases, same graders, pass^{a['repeats']} vs pass^{b['repeats']} "
        f"(runs {a['run_id']} / {b['run_id']}).",
        "",
        f"- **A** `{a['profile']}`: {_fmt_models(a['models'])}",
        f"- **B** `{b['profile']}`: {_fmt_models(b['models'])}",
        "",
    ]
    return "\n".join(header + _render_changes(report) + _render_behaviour(report) + _render_verdict(report)) + "\n"


def _render_changes(report: dict[str, Any]) -> list[str]:
    beyond = report["beyond_config"]
    lines = [
        "## 1. What had to change",
        "",
        f"- **Gateway config:** {len(report['config_diff'])} changed lines in the gateway profile "
        f"(`gateway_config_hash` {report['gateway_config_hash']['a']} → {report['gateway_config_hash']['b']}).",
    ]
    if beyond:
        lines.append("- **Beyond configuration: YES.** These agent bindings differ between the two runs:")
        lines += [f"  - `{x['binding']}`: `{x['a']}` → `{x['b']}`" for x in beyond]
    else:
        lines.append(
            "- **Agent: nothing.** Agent code, prompts, routing policy, skill manifests, graph version and "
            "eval dataset are hash-identical across both runs; only the gateway was restarted."
        )
    if report["config_diff"]:
        lines += ["", "```diff", *report["config_diff"], "```"]

    lines += ["", "### Model-contract adaptations for the models that answered", ""]
    if report["contracts"]:
        lines += ["| Model | Difference from the agent's contract | Absorbed at | Status |", "|---|---|---|---|"]
        for entry in report["contracts"]:
            for diff in entry["differences"]:
                lines.append(
                    f"| {', '.join(entry['matched'])} | {diff['what']} | **{diff['absorbed_at']}** | {diff['status']} |"
                )
    else:
        lines.append("None declared in `gateway/model-contracts.yaml`.")
    return lines


def _render_behaviour(report: dict[str, Any]) -> list[str]:
    s, da, db = report["summary"], report["decision"]["a"], report["decision"]["b"]
    lines = [
        "",
        "## 2. What the swap did to behaviour",
        "",
        f"**Promotion: A {da['decision'].upper()} → B {db['decision'].upper()}** (tier {db['risk_tier']}).",
        *[f"- B: {reason}" for reason in db["reasons"]],
        "",
        "| Gate | A | B |",
        "|---|---|---|",
    ]
    for gate_id, pair in report["gates"].items():
        lines.append(f"| {gate_id} {pair['a']['name']} | {_gate_cell(pair['a'])} | {_gate_cell(pair['b'])} |")
    lines += [
        "",
        f"- G4 detail A: {report['gates']['G4']['a']['detail']}",
        f"- G4 detail B: {report['gates']['G4']['b']['detail']}",
        f"- Cases with identical behaviour: **{s['identical']}/{s['cases']}**",
        f"- Regressed (passed on A, fail on B): **{', '.join(s['regressed']) or 'none'}**",
        f"- Improved: {', '.join(s['improved']) or 'none'}",
        f"- Tool plan changed: {', '.join(s['tool_plan_changed']) or 'none'}",
        f"- Output tokens (sum of per-case means): A {s['tokens_out']['a']} → B {s['tokens_out']['b']}",
        "",
        "| Case | G1 A→B | G2 A→B | G3 A→B | Tools A | Tools B | Calls A→B | p50 ms A→B | Change |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    fmt = {True: "pass", False: "FAIL", None: "-"}
    for c in report["cases"]:
        ga, gb = c["a"]["gates"], c["b"]["gates"]
        gates = [f"{fmt[ga[g]]}→{fmt[gb[g]]}" if ga[g] != gb[g] else fmt[ga[g]] for g in CASE_GATES]
        tools_b = c["b"]["tools"] if c["b"]["tools"] != c["a"]["tools"] else "same"
        lines.append(
            f"| {c['id']} | {' | '.join(gates)} | {c['a']['tools']} | {tools_b} | "
            f"{c['a']['model_calls']}→{c['b']['model_calls']} | "
            f"{c['a']['latency_p50_ms']}→{c['b']['latency_p50_ms']} | {', '.join(c['changes']) or '-'} |"
        )

    diverged = [c for c in report["cases"] if c["regressed"] or c["improved"] or "tool plan" in c["changes"]]
    if diverged:
        lines += ["", "### Where behaviour diverged", ""]
        for c in diverged:
            lines += [
                f"**{c['id']}** ({c['risk']}): _{c['question']}_",
                "",
                f"- A ({c['a']['model']}): {_clip(c['a']['answer'])}",
                f"- B ({c['b']['model']}): {_clip(c['b']['answer'])}",
                *[f"- B failure: {note}" for note in c["b"]["failure_notes"]],
                "",
            ]
    return lines


def _render_verdict(report: dict[str, Any]) -> list[str]:
    s, db, beyond = report["summary"], report["decision"]["b"], report["beyond_config"]
    lines = ["", "## 3. Verdict", ""]
    lines.append(
        "- Mechanically: **configuration only.**"
        if not beyond
        else "- Mechanically: **NOT configuration only** — see the bindings above."
    )
    if s["regressed"]:
        lines.append(
            f"- Behaviourally: **not equivalent.** B regresses {len(s['regressed'])} case(s) "
            f"({', '.join(s['regressed'])}). Before B can serve, each regression needs a fix, "
            "at the gateway (params), in the prompt, or by keeping that intent on A. Any such fix is a new binding "
            "and has to be re-evaluated."
        )
    else:
        lines.append("- Behaviourally: **no gate-level regression** on this dataset.")
    lines.append(f"- Promotion follows the gates, not this summary: B is **{db['decision'].upper()}**.")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("a", help="baseline profile label (e.g. live)")
    parser.add_argument("b", help="swapped profile label (e.g. live-swapped)")
    args = parser.parse_args()
    settings = EvalSettings()
    runs = settings.eval_reports_dir / "runs"
    report = compare(
        _load(runs / f"{args.a}.json"),
        _load(runs / f"{args.b}.json"),
        settings.gateway_config_dir / "profiles",
        settings.gateway_config_dir / "model-contracts.yaml",
    )
    markdown = render(report)
    (settings.eval_reports_dir / "compare-latest.json").write_text(json.dumps(report, indent=2, default=str))
    (settings.eval_reports_dir / "compare-latest.md").write_text(markdown)
    sys.stdout.write(markdown)
    return 1 if report["beyond_config"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
