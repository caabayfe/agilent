from pathlib import Path
from typing import Any

from evals.compare import compare, render


def _run(tools: list[str], model: str, *, passed: bool) -> dict[str, Any]:
    return {
        "trace_id": "t",
        "answer": "ans",
        "alias": "assistant-fast",
        "model_resolved": model,
        "tools": tools,
        "latency_ms": 1000,
        "verdicts": {"G1": {"passed": passed, "notes": [] if passed else ["missing or wrong: x"]}},
        "touched": [],
        "model_calls": 2,
        "tokens_in": 10,
        "tokens_out": 20,
    }


def _report(profile: str, model: str, passed: bool, tools: list[str], prompt: str = "p1") -> dict[str, Any]:
    gate = {"id": "G", "name": "n", "passed": True, "score": 1.0, "detail": "d"}
    return {
        "run_id": profile,
        "mode": profile,
        "repeats": 1,
        "decision": {"decision": "certified", "risk_tier": 2, "reasons": []},
        "gates": dict.fromkeys(("G1", "G2", "G3", "G4"), gate),
        "bindings": {
            "graph_version": "0.1.0",
            "prompt_hash": prompt,
            "routing_policy": "r",
            "gateway_config_hash": profile,
            "skill_manifests_hash": "s",
            "dataset_hash": "d",
        },
        "cases": [
            {
                "id": "orders-status",
                "question": "q",
                "risk": "status_misreport",
                "gates": {"G1": passed, "G2": True, "G3": True},
                "runs": [_run(tools, model, passed=passed)],
            }
        ],
    }


def _profiles(tmp_path: Path) -> Path:
    (tmp_path / "live.yaml").write_text("model: os.environ/MODEL_FAST\n")
    (tmp_path / "live-swapped.yaml").write_text("model: os.environ/MODEL_REASONING\n")
    return tmp_path


def test_config_only_swap_with_behaviour_regression(tmp_path: Path) -> None:
    a = _report("live", "gpt-4.1-mini", True, ["get_order"])
    b = _report("live-swapped", "gpt-5.4", False, ["list_orders", "get_order"])
    report = compare(a, b, _profiles(tmp_path), tmp_path / "none.yaml")

    assert report["beyond_config"] == []
    assert report["config_diff"] == ["-model: os.environ/MODEL_FAST", "+model: os.environ/MODEL_REASONING"]
    assert report["summary"]["regressed"] == ["orders-status"]
    assert report["summary"]["tool_plan_changed"] == ["orders-status"]
    assert report["b"]["models"] == {"assistant-fast": {"gpt-5.4": 1}}
    text = render(report)
    assert "**Agent: nothing.**" in text
    assert "B failure: G1: missing or wrong: x" in text


def test_prompt_change_is_reported_as_beyond_configuration(tmp_path: Path) -> None:
    a = _report("live", "gpt-4.1-mini", True, ["get_order"])
    b = _report("live-swapped", "gpt-5.4", True, ["get_order"], prompt="p2")
    report = compare(a, b, _profiles(tmp_path), tmp_path / "none.yaml")

    assert report["beyond_config"] == [{"binding": "prompt_hash", "a": "p1", "b": "p2"}]
    assert report["summary"]["identical"] == 1
    assert "Beyond configuration: YES" in render(report)


def test_contract_ledger_matches_answering_models(tmp_path: Path) -> None:
    ledger = tmp_path / "contracts.yaml"
    ledger.write_text(
        "models:\n  - match: [gpt-5]\n    differences:\n"
        "      - {what: slow, absorbed_at: not absorbed, status: observed}\n"
    )
    a = _report("live", "gpt-4.1-mini", True, ["get_order"])
    b = _report("live-swapped", "gpt-5.4", True, ["get_order"])
    report = compare(a, b, _profiles(tmp_path), ledger)

    assert [c["matched"] for c in report["contracts"]] == [["gpt-5.4"]]
