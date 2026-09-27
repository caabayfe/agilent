"""Grade any answer, typed by hand, with the same graders the gates and canaries use.

    python -m evals.grade --case billing-outstanding \\
        --answer "Invoice INV-4127 has an outstanding balance of EUR 2,847.60, due 2026-10-15."
    python -m evals.grade --case billing-due --no-evidence --answer "..."   # right answer, no tool called

The evidence is the real tool output from that case's run in the current certificate
(reports/latest.json), read back from the audit trail. If that trace is gone (after
``make reset``), the case is run once through the agent to capture fresh evidence.

Exit code 0 = the graders accept the answer, 1 = rejected. This is the live form of
the canaries: a plausible-looking wrong answer written by the audience, not by us.
"""

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from cfa.agent.service import build_service
from cfa.audit import Action
from cfa.config import agent_settings, identity_settings
from cfa.db import open_pool
from evals import graders
from evals.run import EvalSettings, Harness, grade_answer, load_cases, prepare, run_case


async def _evidence(harness: Harness, settings: EvalSettings, case_id: str) -> tuple[str, str]:
    latest = settings.eval_reports_dir / "latest.json"
    if latest.exists():
        report = json.loads(latest.read_text())
        runs: list[dict[str, Any]] = next((c["runs"] for c in report["cases"] if c["id"] == case_id), [])
        for run in runs:
            if not run.get("trace_id") or run.get("tool_errors"):
                continue
            events = await harness.audit.for_trace(run["trace_id"])
            outputs = [str(e.output.get("content", "")) for e in events if e.action is Action.TOOL_CALL]
            if outputs:
                return graders.evidence_text(outputs), f"audit trace {run['trace_id']} (run {report['run_id']})"
    case = next(c for c in load_cases() if c["id"] == case_id)
    service = build_service(settings=agent_settings(), identity=identity_settings(), audit=harness.audit)
    record = await run_case(harness, service, case, None)
    return record.evidence, f"fresh run, audit trace {record.trace_id}"


async def _grade(case_id: str, answer: str, with_evidence: bool) -> bool:
    settings = EvalSettings()
    cases = [c for c in load_cases() if c["id"] == case_id]
    if not cases:
        raise SystemExit(f"unknown case {case_id!r}; see evals/cases.yaml")
    case = cases[0]
    async with open_pool(settings.eval_database_url.get_secret_value(), max_size=2) as pool:
        harness = await prepare(pool, cases, 1)
        evidence, source = await _evidence(harness, settings, case_id) if with_evidence else ("", "none")
        accepted, notes = grade_answer(harness, case, answer, evidence)

    out = sys.stdout
    out.write(f"Case:      {case_id} ({case['persona']}): {case['question']}\n")
    out.write(f"Answer:    {answer}\n")
    out.write(f"Evidence:  {source}\n")
    out.write("Expected:  " + "; ".join(f"{f.label} = {f.value}" for f in harness.facts[case_id]) + "\n")
    out.write(f"\nVerdict:   {'ACCEPTED' if accepted else 'REJECTED'}\n")
    for note in notes:
        out.write(f"  - {note}\n")
    return accepted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", required=True, help="case id from evals/cases.yaml")
    parser.add_argument("--answer", required=True, help="the answer to grade")
    parser.add_argument("--no-evidence", action="store_true", help="grade as if no tool had been called")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return 0 if asyncio.run(_grade(args.case, args.answer, not args.no_evidence)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
