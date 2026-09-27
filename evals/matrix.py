"""Model matrix: the same eval suite on every model combination in the UI selector.

    python -m evals.matrix                      # every configured model, plus live / live-swapped
    python -m evals.matrix --only gpt-4o,live   # a subset (combination names)
    python -m evals.matrix --repeats 1          # quicker, weaker evidence (pass^1)

For each combination the gateway admin re-points the aliases (the agent is never
touched), the baseline suite runs, and the full report is kept as
``reports/matrix/<name>.json``. The summary is ``reports/matrix-latest.{json,md}``,
also shown in the UI ("Model matrix"). The promotion certificate
(``reports/latest.json``) is not replaced, and the gateway is restored afterwards.

Combinations:
* ``live`` and ``live-swapped``: the named profiles (the two-model production pairing);
* ``<model>``: that one model behind BOTH aliases, so the router's classification call
  and every answer come from it: the model on its own, with the same prompt and graders.
"""

import argparse
import json
import logging
import os
import signal
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from cfa.config import agent_settings
from evals.run import EvalSettings

log = logging.getLogger("evals.matrix")
ALIASES = ("assistant-fast", "assistant-reasoning")
GATES = ("G1", "G2", "G3", "G4")
PROFILES = ("live", "live-swapped")
REFUSED = "provider_refused"


# --------------------------------------------------------------------------- gateway admin


class Admin:
    def __init__(self, url: str, key: str) -> None:
        self.url, self.headers = url.rstrip("/"), {"authorization": f"Bearer {key}"}

    def catalog(self) -> dict[str, Any]:
        response = httpx.get(f"{self.url}/catalog", headers=self.headers, timeout=10)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def select(self, change: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(f"{self.url}/select", headers=self.headers, json=change, timeout=200)
        if response.is_error:
            raise RuntimeError(response.json().get("detail", response.text))
        data: dict[str, Any] = response.json()
        return data


def combinations(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Named production pairings, then every usable non-offline model on both aliases."""
    usable = {p["name"] for p in catalog["profiles"] if not p["missing"]}
    combos: list[dict[str, Any]] = [{"name": p, "change": {"profile": p}} for p in PROFILES if p in usable]
    seen: set[str] = set()
    for model in catalog["models"]:
        resolved = model["resolves_to"].split("/", 1)[-1]
        if not model["available"] or model["vendor"] == "offline" or resolved in seen:
            continue
        seen.add(resolved)
        combos.append({"name": resolved, "change": {"selection": dict.fromkeys(catalog["aliases"], model["id"])}})
    return combos


# --------------------------------------------------------------------------- summary


def _p(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarize(name: str, change: dict[str, Any], report: dict[str, Any], seconds: float) -> dict[str, Any]:
    runs = [(case, run) for case in report["cases"] for run in case["runs"]]
    by_alias: dict[str, dict[str, Any]] = {}
    for alias in ALIASES:
        mine = [run for _, run in runs if run["alias"] == alias]
        latencies = [run["latency_ms"] for run in mine]
        refused = [run for run in mine if REFUSED in (run["model_resolved"] or "")]
        by_alias[alias] = {
            "models": sorted({run["model_resolved"] or "?" for run in mine if run not in refused}),
            "refusals": len(refused),  # the deployment's content filter blocked the request (audited)
            "runs": len(mine),
            "p50_ms": int(statistics.median(latencies)) if latencies else None,
            "p95_ms": _p(latencies, 0.95),
        }
    cases: dict[str, dict[str, Any]] = {}
    for case in report["cases"]:
        notes = sorted(
            {
                f"{g}: {note}"
                for run in case["runs"]
                for g, verdict in run["verdicts"].items()
                for note in verdict["notes"]
                if not verdict["passed"]
            }
        )
        clean = [all(v["passed"] for v in run["verdicts"].values()) for run in case["runs"]]
        repeats_passed = sum(clean)
        # Evidence: the first failing repeat's answer if any failed, else the first answer.
        shown = next((run for run, ok in zip(case["runs"], clean, strict=True) if not ok), None) or (
            case["runs"][0] if case["runs"] else None
        )
        cases[case["id"]] = {
            # None = gate not applicable to this case (e.g. G1 on a denial with no facts).
            "passed": all(ok is not False for ok in case["gates"].values()),
            "failed_gates": [g for g, ok in case["gates"].items() if ok is False],
            "repeats_passed": repeats_passed,
            "repeats": len(case["runs"]),
            "notes": notes[:6],
            "sample_answer": (shown["answer"] or "")[:800] if shown else "",
            "sample_model": shown["model_resolved"] if shown else None,
        }
    return {
        "name": name,
        "change": change,
        "profile": report["mode"],
        "aliases": by_alias,
        "decision": report["decision"]["decision"],
        "reasons": report["decision"]["reasons"],
        "gates": {
            g: {k: report["gates"][g][k] for k in ("passed", "score", "threshold", "detail", "failing_cases")}
            for g in GATES
            if g in report["gates"]
        },
        "canaries_ok": report["canaries"]["ok"],
        "tokens_in": sum(run.get("tokens_in") or 0 for _, run in runs),
        "tokens_out": sum(run.get("tokens_out") or 0 for _, run in runs),
        "model_calls": sum(run.get("model_calls") or 0 for _, run in runs),
        "cases": cases,
        "repeats": report["repeats"],
        "run_id": report["run_id"],
        "bindings": report["bindings"],
        "duration_s": round(seconds),
    }


def _models(alias: dict[str, Any]) -> str:
    refusals = alias.get("refusals", 0)
    return ", ".join(alias["models"]) + (f" ({refusals} refused by content filter)" if refusals else "")


def _mark(ok: bool | None) -> str:
    return "—" if ok is None else ("PASS" if ok else "FAIL")


def render(matrix: dict[str, Any]) -> str:
    rows = [r for r in matrix["results"] if "error" not in r]
    out = [
        "# Model matrix",
        "",
        f"{matrix['created_at']} · pass^{matrix['repeats']} · {len(matrix['case_ids'])} cases per combination · "
        "same agent code, prompt, routing policy, skills and graders in every column: only the gateway changed.",
        "",
        "## Gates per combination",
        "",
        "| Combination | fast → | reasoning → | Decision | G1 | G2 | G3 | G4 p95/budget | Cases | Tokens in/out |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        fast, reasoning = (_models(r["aliases"][a]) for a in ALIASES)
        gates = [
            f"{_mark(r['gates'][g]['passed'])} {r['gates'][g]['score']:.2f}" if g in r["gates"] else "—"
            for g in ("G1", "G2", "G3")
        ]
        passed = sum(c["passed"] for c in r["cases"].values())
        g4 = r["gates"].get("G4", {})
        worst = f"{g4.get('score', 0):.0f}/{g4.get('threshold', 0):.0f} ms" if g4 else "—"
        out.append(
            f"| **{r['name']}** | {fast} | {reasoning} | {r['decision']} | {' | '.join(gates)} | "
            f"{_mark(g4.get('passed'))} {worst} | {passed}/{len(r['cases'])} | {r['tokens_in']}/{r['tokens_out']} |"
        )
    for r in matrix["results"]:
        if "error" in r:
            out.append(f"| **{r['name']}** | | | ERROR | | | | | | {r['error']} |")
    out += ["", "## Case by combination", "", "| Case | " + " | ".join(r["name"] for r in rows) + " |"]
    out.append("|---|" + "---|" * len(rows))
    for case_id in matrix["case_ids"]:
        cells = []
        for r in rows:
            c = r["cases"].get(case_id)
            cells.append("—" if c is None else ("✓" if c["passed"] else f"✗ {'+'.join(c['failed_gates'])}"))
        out.append(f"| {case_id} | " + " | ".join(cells) + " |")
    out += ["", "## Why each failure failed", ""]
    for r in rows:
        failures = {cid: c for cid, c in r["cases"].items() if not c["passed"]}
        if not failures:
            continue
        out.append(f"**{r['name']}**")
        out.append("")
        for cid, c in failures.items():
            out.append(f"- `{cid}` ({c['repeats_passed']}/{c['repeats']} repeats clean): {'; '.join(c['notes'])}")
        out.append("")
    reruns = [r for r in matrix["results"] if r.get("history")]
    if reruns:
        out += ["## Re-runs (earlier attempts are kept, not hidden)", ""]
        for r in reruns:
            for h in r["history"]:
                failed = "; ".join(f"`{cid}`: {', '.join(notes)}" for cid, notes in h.get("failed_cases", {}).items())
                out.append(f"- **{r['name']}** earlier run {h.get('run_id')}: {h.get('decision', 'error')}. {failed}")
        out.append("")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- main


def _write(directory: Path, stem: str, data: dict[str, Any], markdown: str | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.json").write_text(json.dumps(data, indent=2, default=str))
    if markdown is not None:
        (directory / f"{stem}.md").write_text(markdown)


def _brief(result: dict[str, Any]) -> dict[str, Any]:
    """A replaced attempt, kept so a re-run cannot silently hide an earlier failure."""
    if "error" in result:
        return {"run_id": None, "error": result["error"]}
    return {
        "run_id": result["run_id"],
        "decision": result["decision"],
        "gates": {g: v["passed"] for g, v in result["gates"].items()},
        "failed_cases": {cid: c["notes"] for cid, c in result["cases"].items() if not c["passed"]},
    }


def run_isolated(name: str, repeats: int, reports: Path) -> dict[str, Any]:
    """One `evals.run` process per combination, exactly like `make eval`: model clients
    cached by the agent's libraries are bound to their event loop, so reusing a process
    across combinations would crash runs (and those crashes would fail every gate)."""
    argv = [sys.executable, "-m", "evals.run", "--repeats", str(repeats), "--no-publish"]
    argv += ["--label", name, "--label-dir", "matrix"]
    completed = subprocess.run(argv, check=False, stdout=subprocess.DEVNULL)  # noqa: S603 - fixed argv
    if completed.returncode != 0:
        raise RuntimeError(f"evals.run exited with {completed.returncode}")
    data: dict[str, Any] = json.loads((reports / "matrix" / f"{name}.json").read_text())
    return data


def rebuild(reports: Path) -> int:
    """Regenerate matrix-latest from reports/matrix/<name>.json (e.g. after changing the summary)."""
    matrix: dict[str, Any] = json.loads((reports / "matrix-latest.json").read_text())
    results = []
    for old in matrix["results"]:
        path = reports / "matrix" / f"{old['name']}.json"
        if "error" in old or not path.exists():
            results.append(old)
            continue
        fresh = summarize(old["name"], old["change"], json.loads(path.read_text()), old.get("duration_s", 0))
        if "history" in old:
            fresh["history"] = old["history"]
        results.append(fresh)
    matrix["results"] = results
    _write(reports, "matrix-latest", matrix, render(matrix))
    sys.stdout.write(render(matrix))
    return 0


def _run_one(admin: Admin, combo: dict[str, Any], repeats: int, reports: Path) -> dict[str, Any]:
    began = time.monotonic()
    try:
        admin.select(combo["change"])
        report = run_isolated(combo["name"], repeats, reports)
    except Exception as exc:  # one broken model must not abort the matrix
        log.exception("combination %s failed", combo["name"])
        return {"name": combo["name"], "change": combo["change"], "error": str(exc)[:300]}
    summary = summarize(combo["name"], combo["change"], report, time.monotonic() - began)
    gates = " ".join(f"{g}={_mark(v['passed'])}" for g, v in summary["gates"].items())
    log.info("%-14s %s %s (%ss)", combo["name"], summary["decision"], gates, summary["duration_s"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="comma-separated combination names")
    parser.add_argument("--repeats", type=int, default=3, help="runs per case (pass^k)")
    parser.add_argument("--cooldown", type=int, default=60, help="seconds between combinations (skill rate limit)")
    parser.add_argument("--rebuild", action="store_true", help="re-summarise the saved reports; run nothing")
    args = parser.parse_args()
    if args.rebuild:
        return rebuild(EvalSettings().eval_reports_dir)
    # `docker compose stop`/Ctrl-C must still reach the `finally` below that restores the gateway.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore", "mcp", "evals.run", "cfa"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = EvalSettings()
    admin = Admin(
        os.environ.get("GATEWAY_ADMIN_URL", "http://litellm:4001"), agent_settings().gateway_api_key.get_secret_value()
    )
    start = admin.catalog()
    combos = combinations(start)
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        combos = [c for c in combos if c["name"] in wanted]
    log.info("combinations: %s", ", ".join(c["name"] for c in combos))
    reports = settings.eval_reports_dir
    order = [c["name"] for c in combinations(start)]
    kept: dict[str, dict[str, Any]] = {}
    case_ids: list[str] = []
    if args.only and (reports / "matrix-latest.json").exists():
        # Re-running a subset: keep the other columns, and keep the replaced result as history.
        previous = json.loads((reports / "matrix-latest.json").read_text())
        kept = {r["name"]: r for r in previous["results"]}
        case_ids = previous["case_ids"]
    results: list[dict[str, Any]] = []
    try:
        for index, combo in enumerate(combos):
            if index:
                time.sleep(args.cooldown)
            result = _run_one(admin, combo, args.repeats, reports)
            case_ids = case_ids or list(result.get("cases", {}))
            if old := kept.pop(combo["name"], None):
                result["history"] = [*old.pop("history", []), _brief(old)]
            results.append(result)
            merged = sorted(
                [*results, *kept.values()], key=lambda r: order.index(r["name"]) if r["name"] in order else 99
            )
            matrix = {
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "repeats": args.repeats,
                "case_ids": case_ids,
                "results": merged,
                "complete": index == len(combos) - 1,
            }
            _write(reports, "matrix-latest", matrix, render(matrix))  # partial results survive an abort
    finally:
        restore = (
            {"profile": start["profile"]}
            if start["profile"] != "custom"
            else {"selection": {a: m for a, m in start["selection"].items() if m}}
        )
        admin.select(restore)
        log.info("gateway restored to %s", start["profile"])
    sys.stdout.write(render({"created_at": "", "repeats": args.repeats, "case_ids": case_ids, "results": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
