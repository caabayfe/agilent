from typing import Any

import pytest

from evals.gates import GateResult, p95, worst_class_p95
from evals.promotion import Decision, decide, derive_tier, load_policy, skill_manifests

POLICY = load_policy()


def gates(**failed: bool) -> dict[str, GateResult]:
    return {
        g: GateResult(g, g, "", not failed.get(g, False), 1.0, 1.0, f"{g} detail", []) for g in ("G1", "G2", "G3", "G4")
    }


def test_all_green_is_certified() -> None:
    assert decide(gates(), canaries_ok=True, risk_tier=2, policy=POLICY).decision is Decision.CERTIFIED


def test_latency_is_advisory_at_tier_1() -> None:
    result = decide(gates(G4=True), canaries_ok=True, risk_tier=1, policy=POLICY)
    assert result.decision is Decision.CERTIFIED
    assert result.reasons  # the advisory failure is still reported


def test_latency_blocks_at_tier_2() -> None:
    result = decide(gates(G4=True), canaries_ok=True, risk_tier=2, policy=POLICY)
    assert result.decision is Decision.BLOCKED
    assert "G4" in result.reasons[0]


@pytest.mark.parametrize("gate", ["G1", "G2", "G3"])
def test_accuracy_leakage_routing_always_mandatory(gate: str) -> None:
    for tier in (1, 2):
        assert (
            decide(gates(**{gate: True}), canaries_ok=True, risk_tier=tier, policy=POLICY).decision is Decision.BLOCKED
        )


def test_failed_canaries_invalidate_everything() -> None:
    assert decide(gates(), canaries_ok=False, risk_tier=1, policy=POLICY).decision is Decision.HARNESS_INVALID


def test_tier_is_derived_from_manifests() -> None:
    manifests: dict[str, Any] = skill_manifests()
    assert derive_tier({"mcp-customer/orders", "mcp-customer/service"}, manifests)[0] == 1
    tier, sources = derive_tier({"mcp-customer/orders", "mcp-customer/billing"}, manifests)
    assert tier == 2
    assert sources == {"mcp-customer/billing": 2, "mcp-customer/orders": 1}


def test_p95() -> None:
    assert p95([100] * 19 + [9000]) == 100
    assert p95([100] * 18 + [9000, 9000]) == 9000
    assert p95([]) == 0


def test_latency_gate_uses_the_slowest_model_class() -> None:
    # A slow path that is 3% of traffic vanishes in a blended p95; per-class p95 keeps it visible.
    runs = {"assistant-fast": [1000] * 97, "assistant-reasoning": [6000] * 3}
    assert p95([v for values in runs.values() for v in values]) == 1000
    assert worst_class_p95(runs) == ("assistant-reasoning", 6000)
    assert worst_class_p95({}) == ("none", 0)
