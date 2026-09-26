"""Aggregate per-case verdicts into gate results."""

import math
from collections.abc import Mapping, Sequence
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


@dataclass(slots=True)
class CaseResult:
    case: dict[str, Any]
    runs: list[RunRecord] = field(default_factory=list)

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


def worst_class_p95(latencies_by_alias: Mapping[str, Sequence[int]]) -> tuple[str, int]:
    """p95 of the slowest model class. A blended p95 lets a slow but rare path (e.g.
    troubleshooting on the reasoning model) hide behind the fast majority."""
    if not latencies_by_alias:
        return "none", 0
    return max(((alias, p95(values)) for alias, values in latencies_by_alias.items()), key=lambda item: item[1])


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
    budget = int(spec["p95_latency_ms"])
    by_alias: dict[str, list[int]] = {}
    for r in results:
        for run in r.runs:
            by_alias.setdefault(run.alias or "unknown", []).append(run.latency_ms)
    worst_alias, observed = worst_class_p95(by_alias)
    breakdown = ", ".join(f"{alias} p95={p95(v)}ms" for alias, v in sorted(by_alias.items()))
    slow = sorted({r.case["id"] for r in results for run in r.runs if run.latency_ms > budget})
    gates["G4"] = GateResult(
        "G4",
        spec["name"],
        spec["catches"],
        observed <= budget,
        float(observed),
        float(budget),
        f"slowest model class p95 {observed}ms ({worst_alias}) vs budget {budget}ms ({breakdown})",
        slow,
    )
    return gates
