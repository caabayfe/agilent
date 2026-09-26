"""Aggregate per-case verdicts into gate results."""

import math
from collections.abc import Sequence
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
    touched: list[str] = field(default_factory=list)  # skill audiences reached


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
    latencies = [run.latency_ms for r in results for run in r.runs]
    observed = p95(latencies)
    by_alias: dict[str, list[int]] = {}
    for r in results:
        for run in r.runs:
            by_alias.setdefault(run.alias or "unknown", []).append(run.latency_ms)
    breakdown = ", ".join(f"{alias} p95={p95(v)}ms" for alias, v in sorted(by_alias.items()))
    slow = sorted({r.case["id"] for r in results for run in r.runs if run.latency_ms > budget})
    gates["G4"] = GateResult(
        "G4",
        spec["name"],
        spec["catches"],
        observed <= budget,
        float(observed),
        float(budget),
        f"p95 end-to-end latency {observed}ms vs budget {budget}ms ({breakdown})",
        slow,
    )
    return gates
