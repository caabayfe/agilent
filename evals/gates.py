"""Aggregate per-case verdicts into gate results."""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

GATE_IDS = ("G1", "G2", "G3", "G4")


@dataclass(slots=True)
class RunRecord:
    """One execution of one case."""

    trace_id: str
    answer: str
    alias: str | None
    model_resolved: str | None
    tools: list[str]
    latency_ms: int
    verdicts: dict[str, dict[str, Any]]  # gate id -> Verdict.to_dict()
    evidence: str = ""
    touched: list[str] = field(default_factory=list)  # skill components reached, e.g. mcp-customer/billing
    model_calls: int = 0  # route + assistant turns; a provider swap can change the ReAct loop length
    tokens_in: int = 0
    tokens_out: int = 0
    tool_errors: int = 0  # tool calls that returned {"error": ...} (denial, outage, rate limit)


@dataclass(slots=True)
class CaseResult:
    case: dict[str, Any]
    runs: list[RunRecord] = field(default_factory=list)

    def reference_evidence(self) -> str:
        """Tool evidence from the first repeat whose tool calls all succeeded (else repeat 0).

        Canary controls are graded against this; a transient outage on one repeat
        must not make the harness reject a correct control answer.
        """
        clean = [run for run in self.runs if run.tool_errors == 0 and run.evidence]
        return (clean or self.runs)[0].evidence if self.runs else ""

    def passed(self, gate: str) -> bool | None:
        verdicts = [run.verdicts.get(gate) for run in self.runs]
        if not verdicts or any(v is None for v in verdicts):
            return None  # gate not applicable to this case
        return all(v["passed"] for v in verdicts if v is not None)  # pass^k


@dataclass(slots=True)
class GateResult:
    id: str
    name: str
    catches: str
    passed: bool
    score: float
    threshold: float
    detail: str
    failing_cases: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "catches": self.catches,
            "passed": self.passed,
            "score": round(self.score, 3),
            "threshold": self.threshold,
            "detail": self.detail,
            "failing_cases": self.failing_cases,
        }


def latency_budgets(spec: int | Mapping[str, int]) -> Callable[[str | None], int]:
    """G4 budget per model class. An alias without its own budget gets the strictest one."""
    if isinstance(spec, int):
        return lambda _alias: spec
    budgets = {str(k): int(v) for k, v in spec.items()}
    strictest = min(budgets.values())
    return lambda alias: budgets.get(alias or "", strictest)


def worst_class_vs_budget(
    latencies_by_alias: Mapping[str, Sequence[int]], budget_of: Callable[[str | None], int]
) -> tuple[str, int, int]:
    """The model class furthest over (or closest to) its own budget: (alias, p95, budget)."""
    if not latencies_by_alias:
        return "none", 0, budget_of(None)
    alias = max(latencies_by_alias, key=lambda a: p95(latencies_by_alias[a]) / budget_of(a))
    return alias, p95(latencies_by_alias[alias]), budget_of(alias)


def p95(values: Sequence[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def score_gates(results: Sequence[CaseResult], policy: dict[str, Any]) -> dict[str, GateResult]:
    gates: dict[str, GateResult] = {}
    for gate in ("G1", "G2", "G3"):
        spec = policy["gates"][gate]
        applicable = [r for r in results if r.passed(gate) is not None]
        failing = [r.case["id"] for r in applicable if not r.passed(gate)]
        score = (len(applicable) - len(failing)) / len(applicable) if applicable else 1.0
        threshold = float(spec["threshold"])
        gates[gate] = GateResult(
            gate,
            spec["name"],
            spec["catches"],
            score >= threshold,
            score,
            threshold,
            f"{len(applicable) - len(failing)}/{len(applicable)} cases pass on every repeat",
            failing,
        )

    spec = policy["gates"]["G4"]
    by_alias: dict[str, list[int]] = {}
    for r in results:
        for run in r.runs:
            by_alias.setdefault(run.alias or "unknown", []).append(run.latency_ms)
    budget_of = latency_budgets(spec["p95_latency_ms"])
    worst_alias, observed, budget = worst_class_vs_budget(by_alias, budget_of)
    breakdown = ", ".join(f"{alias} p95={p95(v)}ms/{budget_of(alias)}ms" for alias, v in sorted(by_alias.items()))
    slow = sorted({r.case["id"] for r in results for run in r.runs if run.latency_ms > budget_of(run.alias)})
    gates["G4"] = GateResult(
        "G4",
        spec["name"],
        spec["catches"],
        all(p95(v) <= budget_of(alias) for alias, v in by_alias.items()),
        float(observed),
        float(budget),
        f"worst model class vs its budget: {worst_alias} p95 {observed}ms vs {budget}ms ({breakdown})",
        slow,
    )
    return gates
