"""Eval harness CLI.

    python -m evals.run              # baseline: gates + canaries + promotion decision
    python -m evals.run --mutants    # mutation runs: mutant x gate matrix
    python -m evals.run --repeats 3  # pass^k (default: 1 on the fake provider, 3 live)

Runs the exact service the BFF serves (``build_service``), against the real skill
servers and gateway, then grades every run from the audit lineage.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from cfa.agent.graph import AgentConfig
from cfa.agent.service import AssistantService, build_service
from cfa.audit import Action, AuditLog
from cfa.config import agent_settings, identity_settings
from cfa.db import Pool, open_pool
from cfa.identity.client import IdpClient
from cfa.identity.tokens import TokenValidator, UserClaims
from evals import graders
from evals.gates import CaseResult, GateResult, RunRecord, score_gates
from evals.ground_truth import Fact, GroundTruth
from evals.mutants import MUTANTS, Mutant
from evals.promotion import (
    PromotionDecision,
    current_bindings,
    decide,
    derive_tier,
    gateway_profile,
    load_policy,
    skill_manifests,
)

EVALS_DIR = Path(__file__).parent
log = logging.getLogger("evals")


class EvalSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    eval_database_url: SecretStr
    gateway_config_dir: Path = Path("/app/gateway")
    eval_reports_dir: Path = EVALS_DIR / "reports"
    eval_concurrency: int = 4


@dataclass(slots=True)
class Persona:
    token: str
    claims: UserClaims
    foreign: frozenset[str]


@dataclass(slots=True)
class Harness:
    truth: GroundTruth
    audit: AuditLog
    personas: dict[str, Persona]
    facts: dict[str, list[Fact]]
    semaphore: asyncio.Semaphore


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = yaml.safe_load((EVALS_DIR / "cases.yaml").read_text())["cases"]
    return cases


def load_canaries() -> list[dict[str, Any]]:
    canaries: list[dict[str, Any]] = yaml.safe_load((EVALS_DIR / "canaries.yaml").read_text())["canaries"]
    return canaries


async def prepare(pool: Pool, cases: list[dict[str, Any]], concurrency: int) -> Harness:
    identity = identity_settings()
    idp = IdpClient(identity.idp_url, identity.agent_client_id, identity.agent_client_secret.get_secret_value())
    validator = TokenValidator(f"{identity.idp_url}/.well-known/jwks.json", identity.idp_issuer)
    truth = GroundTruth(pool)
    personas: dict[str, Persona] = {}
    for username in sorted({c["persona"] for c in cases}):
        token = await idp.login(username)
        claims = UserClaims.model_validate(await validator.decode(token, identity.bff_audience))
        personas[username] = Persona(token, claims, await truth.foreign_identifiers(claims.customer_id))
    facts = {c["id"]: [await truth.resolve(spec) for spec in c.get("facts", [])] for c in cases}
    return Harness(truth, AuditLog(pool), personas, facts, asyncio.Semaphore(concurrency))


async def run_case(
    harness: Harness, service: AssistantService, case: dict[str, Any], mutant_header: str | None
) -> RunRecord:
    persona = harness.personas[case["persona"]]
    async with harness.semaphore:
        started = time.perf_counter()
        try:
            result = await service.ask(
                user_token=persona.token,
                user=persona.claims,
                question=case["question"],
                thread_id=f"eval:{uuid.uuid4().hex}",
                eval_mutant=mutant_header,
            )
        except Exception as exc:  # noqa: BLE001 - a crashed run is a failed run, not a crashed harness
            log.warning("case %s crashed: %s", case["id"], exc)
            failed = {"passed": False, "notes": [f"run crashed: {type(exc).__name__}"]}
            return RunRecord(
                "",
                "",
                None,
                None,
                [],
                int((time.perf_counter() - started) * 1000),
                {"G1": failed, "G2": failed, "G3": failed},
            )
    events = await harness.audit.for_trace(result.trace_id)
    tool_events = [e for e in events if e.action is Action.TOOL_CALL]
    decisions = [e.model_dump(mode="json") for e in events if e.action is Action.ACCESS_DECISION]
    called = [e.name or "" for e in tool_events]
    evidence = graders.evidence_text(str(e.output.get("content", "")) for e in tool_events)

    verdicts: dict[str, dict[str, Any]] = {}
    if harness.facts[case["id"]]:
        verdicts["G1"] = graders.grade_grounded(
            result.answer, harness.facts[case["id"]], evidence, case["question"]
        ).to_dict()
    verdicts["G2"] = graders.grade_leakage(
        answer=result.answer,
        question=case["question"],
        customer_id=persona.claims.customer_id,
        foreign=persona.foreign,
        access_decisions=decisions,
        forbid_allow_on=case.get("forbid_allow_on"),
    ).to_dict()
    verdicts["G3"] = graders.grade_routing(
        called=called,
        alias=result.model_alias,
        expected_tools=case.get("expected_tools", []),
        allowed_tools=case.get("allowed_tools", case.get("expected_tools", [])),
        expected_alias=case["expected_alias"],
    ).to_dict()
    return RunRecord(
        result.trace_id,
        result.answer,
        result.model_alias,
        result.model_resolved,
        called,
        result.latency_ms,
        verdicts,
        evidence,
        sorted({d["component"] for d in decisions}),
    )


async def run_suite(
    harness: Harness,
    service: AssistantService,
    cases: list[dict[str, Any]],
    repeats: int,
    mutant_header: str | None = None,
) -> list[CaseResult]:
    results = [CaseResult(case) for case in cases]
    jobs = [(r, run_case(harness, service, r.case, mutant_header)) for r in results for _ in range(repeats)]
    records = await asyncio.gather(*(job for _, job in jobs))
    for (result, _), record in zip(jobs, records, strict=True):
        result.runs.append(record)
    return results


def check_canaries(
    harness: Harness, baseline: list[CaseResult], canaries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Feed plausible-looking wrong answers (and correct controls) to the graders."""
    by_case = {r.case["id"]: r for r in baseline}
    outcomes = []
    for canary in canaries:
        result = by_case[canary["case"]]
        case, persona = result.case, harness.personas[result.case["persona"]]
        real_evidence = result.runs[0].evidence if result.runs else ""

        def accepted(
            answer: str, evidence: str, case: dict[str, Any] = case, persona: Persona = persona
        ) -> tuple[bool, list[str]]:
            g1 = graders.grade_grounded(answer, harness.facts[case["id"]], evidence, case["question"])
            g2 = graders.grade_leakage(
                answer=answer,
                question=case["question"],
                customer_id=persona.claims.customer_id,
                foreign=persona.foreign,
                access_decisions=[],
                forbid_allow_on=None,
            )
            return g1.passed and g2.passed, g1.notes + g2.notes

        doctored_evidence = "" if canary.get("evidence") == "none" else real_evidence
        doctored_ok, doctored_notes = accepted(canary["doctored"], doctored_evidence)
        control_ok, control_notes = accepted(canary["control"], real_evidence)
        outcomes.append(
            {
                "id": canary["id"],
                "case": canary["case"],
                "corruption": canary["corruption"],
                "doctored": canary["doctored"],
                "doctored_rejected": not doctored_ok,
                "rejected_because": doctored_notes,
                "control_accepted": control_ok,
                "control_notes": control_notes,
                "ok": (not doctored_ok) and control_ok,
            }
        )
    return outcomes


def _case_report(result: CaseResult) -> dict[str, Any]:
    case = result.case
    return {
        "id": case["id"],
        "persona": case["persona"],
        "question": case["question"],
        "risk": case["risk"],
        "why_it_matters": case["why_it_matters"],
        "gates": {g: result.passed(g) for g in ("G1", "G2", "G3")},
        "runs": [{k: v for k, v in asdict(run).items() if k != "evidence"} for run in result.runs],
    }


def _touched(results: list[CaseResult]) -> set[str]:
    return {skill for r in results for run in r.runs for skill in run.touched}


async def baseline(settings: EvalSettings, repeats: int) -> dict[str, Any]:
    cases, canaries, policy = load_cases(), load_canaries(), load_policy()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    mode = gateway_profile(settings.gateway_config_dir)
    bindings = current_bindings(settings.gateway_config_dir)
    async with open_pool(settings.eval_database_url.get_secret_value(), max_size=8) as pool:
        harness = await prepare(pool, cases, settings.eval_concurrency)
        service = build_service(settings=agent_settings(), identity=identity_settings(), audit=harness.audit)
        results = await run_suite(harness, service, cases, repeats)
        gates = score_gates(results, policy)
        canary_outcomes = check_canaries(harness, results, canaries)
        canaries_ok = all(c["ok"] for c in canary_outcomes)

        manifests = skill_manifests()
        bound = set(manifests)  # every skill the agent holds tools for is in its blast radius
        tier, tier_sources = derive_tier(bound | _touched(results), manifests)
        decision = decide(gates, canaries_ok, tier, policy)
        report = {
            "run_id": run_id,
            "kind": "baseline",
            "asset": policy["asset"],
            "mode": mode,
            "repeats": repeats,
            "created_at": datetime.now(UTC).isoformat(),
            "decision": _decision_dict(decision, tier_sources),
            "gates": {g: r.to_dict() for g, r in gates.items()},
            "canaries": {"ok": canaries_ok, "results": canary_outcomes},
            "cases": [_case_report(r) for r in results],
            "bindings": bindings,
        }
        async with pool.connection() as conn:
            await conn.execute(
                "INSERT INTO registry.assets (asset_id, decision, risk_tier, run_id, mode, bindings, summary) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    policy["asset"],
                    decision.decision.value,
                    tier,
                    run_id,
                    mode,
                    Jsonb(bindings),
                    Jsonb({"reasons": decision.reasons, "gates": {g: r.passed for g, r in gates.items()}}),
                ),
            )
    _write(settings.eval_reports_dir, "latest", report, render_baseline(report))
    return report


def _decision_dict(decision: PromotionDecision, tier_sources: dict[str, int]) -> dict[str, Any]:
    return {
        "decision": decision.decision.value,
        "risk_tier": decision.risk_tier,
        "tier_sources": tier_sources,
        "mandatory": decision.mandatory,
        "advisory": decision.advisory,
        "reasons": decision.reasons,
    }


async def mutation_runs(settings: EvalSettings) -> dict[str, Any]:
    cases, policy = load_cases(), load_policy()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows = []
    async with open_pool(settings.eval_database_url.get_secret_value(), max_size=8) as pool:
        harness = await prepare(pool, cases, settings.eval_concurrency)
        variants: list[Mutant | None] = [None, *MUTANTS]
        for mutant in variants:
            config = mutant.config if mutant else AgentConfig()
            service = build_service(
                settings=agent_settings(), identity=identity_settings(), audit=harness.audit, config=config
            )
            log.info("running %s", mutant.id if mutant else "baseline")
            results = await run_suite(harness, service, cases, 1, mutant.eval_mutant_header if mutant else None)
            gates = score_gates(results, policy)
            rows.append(_mutant_row(mutant, gates))
    report = {
        "run_id": run_id,
        "kind": "mutants",
        "mode": gateway_profile(settings.gateway_config_dir),
        "created_at": datetime.now(UTC).isoformat(),
        "rows": rows,
        "all_killed": all(r["killed"] for r in rows if r["id"] != "baseline"),
    }
    _write(settings.eval_reports_dir, "mutants-latest", report, render_mutants(report))
    return report


def _mutant_row(mutant: Mutant | None, gates: dict[str, GateResult]) -> dict[str, Any]:
    failed = [g for g, r in gates.items() if not r.passed]
    base = {
        "gates": {
            g: {"passed": r.passed, "score": round(r.score, 3), "failing_cases": r.failing_cases}
            for g, r in gates.items()
        },
        "failed_gates": failed,
    }
    if mutant is None:
        return {"id": "baseline", "name": "baseline", "regression": "none", "target_gate": None, "killed": None} | base
    return {
        "id": mutant.id,
        "name": mutant.name,
        "regression": mutant.regression,
        "target_gate": mutant.target_gate,
        "killed": mutant.target_gate in failed,
    } | base


# ------------------------------------------------------------------ rendering
def render_baseline(report: dict[str, Any]) -> str:
    d = report["decision"]
    lines = [
        f"# Eval report {report['run_id']} ({report['mode']} mode, pass^{report['repeats']})",
        "",
        f"**Decision: {d['decision'].upper()}** (risk tier {d['risk_tier']}, derived from {d['tier_sources']})",
        "",
        *[f"- {reason}" for reason in d["reasons"]],
        "",
        "## Gates",
        "",
        "| Gate | Result | Score | Threshold | Catches |",
        "|---|---|---|---|---|",
    ]
    for gate in report["gates"].values():
        mark = "PASS" if gate["passed"] else "FAIL"
        lines.append(
            f"| {gate['id']} {gate['name']} | {mark} | {gate['score']} | {gate['threshold']} | {gate['catches']} |"
        )
    lines += ["", f"## Grader canaries: {'all rejected' if report['canaries']['ok'] else 'HARNESS INVALID'}", ""]
    for c in report["canaries"]["results"]:
        lines.append(f"- {'OK ' if c['ok'] else 'BAD'} {c['id']}: {'; '.join(c['rejected_because']) or 'NOT REJECTED'}")
    lines += ["", "## Cases", "", "| Case | Risk | G1 | G2 | G3 |", "|---|---|---|---|---|"]
    fmt = {True: "pass", False: "FAIL", None: "-"}
    for case in report["cases"]:
        g = case["gates"]
        lines.append(f"| {case['id']} | {case['risk']} | {fmt[g['G1']]} | {fmt[g['G2']]} | {fmt[g['G3']]} |")
    lines += ["", "## Bindings", "", *[f"- {k}: `{v}`" for k, v in report["bindings"].items()], ""]
    return "\n".join(lines)


def render_mutants(report: dict[str, Any]) -> str:
    lines = [
        f"# Mutation runs {report['run_id']} ({report['mode']} mode)",
        "",
        f"**{'Every mutant killed by its target gate.' if report['all_killed'] else 'SOME MUTANTS SURVIVED.'}**",
        "",
        "| Mutant | Regression | Target | G1 | G2 | G3 | G4 | Killed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        cells = ["caught" if not row["gates"][g]["passed"] else "." for g in ("G1", "G2", "G3", "G4")]
        killed = "-" if row["killed"] is None else ("yes" if row["killed"] else "NO")
        target = row["target_gate"] or "-"
        lines.append(f"| {row['id']} {row['name']} | {row['regression']} | {target} | {' | '.join(cells)} | {killed} |")
    return "\n".join(lines) + "\n"


def _write(directory: Path, stem: str, report: dict[str, Any], markdown: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.json").write_text(json.dumps(report, indent=2, default=str))
    (directory / f"{stem}.md").write_text(markdown)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mutants", action="store_true", help="run mutation runs instead of the baseline")
    parser.add_argument("--repeats", type=int, default=None, help="runs per case (pass^k)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = EvalSettings()
    if args.mutants:
        report = asyncio.run(mutation_runs(settings))
        sys.stdout.write(render_mutants(report))
        return 0 if report["all_killed"] else 1
    repeats = args.repeats or (1 if gateway_profile(settings.gateway_config_dir) == "fake" else 3)
    report = asyncio.run(baseline(settings, repeats))
    sys.stdout.write(render_baseline(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
