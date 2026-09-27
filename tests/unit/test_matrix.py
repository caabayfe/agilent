"""Model matrix: combination selection and per-combination summary."""

from typing import Any

from evals.matrix import _brief, combinations, render, summarize


def _model(mid: str, resolves: str, vendor: str = "Azure OpenAI", available: bool = True) -> dict[str, Any]:
    return {"id": mid, "resolves_to": resolves, "vendor": vendor, "available": available}


def test_combinations_cover_profiles_and_each_distinct_usable_model() -> None:
    catalog = {
        "aliases": ["assistant-fast", "assistant-reasoning"],
        "profiles": [{"name": "live", "missing": []}, {"name": "live-swapped", "missing": []}],
        "models": [
            _model("env-fast", "azure/gpt-4.1-mini"),
            _model("dup", "azure/gpt-4.1-mini"),
            _model("env-alt", "", available=False),
            _model("fake-fast", "openai/fake-fast", vendor="offline"),
        ],
    }
    combos = combinations(catalog)
    assert [c["name"] for c in combos] == ["live", "live-swapped", "gpt-4.1-mini"]
    assert combos[2]["change"] == {"selection": {"assistant-fast": "env-fast", "assistant-reasoning": "env-fast"}}


def _run(alias: str, ok: bool, latency: int, answer: str) -> dict[str, Any]:
    verdict = {"passed": ok, "notes": [] if ok else ["unsupported identifier: SO-10231"]}
    return {
        "alias": alias,
        "model_resolved": "azure/m",
        "latency_ms": latency,
        "answer": answer,
        "verdicts": {"G1": verdict, "G2": {"passed": True, "notes": []}},
        "tokens_in": 10,
        "tokens_out": 2,
        "model_calls": 2,
    }


def test_summary_keeps_the_failing_answer_as_evidence() -> None:
    gate = {"passed": False, "score": 0.5, "threshold": 0.95, "detail": "1/2", "failing_cases": ["b"]}
    report = {
        "mode": "custom",
        "repeats": 2,
        "run_id": "r",
        "bindings": {},
        "canaries": {"ok": True},
        "decision": {"decision": "blocked", "reasons": ["G1 failed"]},
        "gates": {"G1": gate},
        "cases": [
            {"id": "a", "gates": {"G1": None, "G2": True}, "runs": [_run("assistant-fast", True, 100, "fine")] * 2},
            {
                "id": "b",
                "gates": {"G1": False},
                "runs": [
                    _run("assistant-reasoning", True, 300, "good"),
                    _run("assistant-reasoning", False, 500, "bad"),
                ],
            },
        ],
    }
    summary = summarize("m", {"profile": "x"}, report, 12.4)
    assert summary["cases"]["b"] == {
        "passed": False,
        "failed_gates": ["G1"],
        "repeats_passed": 1,
        "repeats": 2,
        "notes": ["G1: unsupported identifier: SO-10231"],
        "sample_answer": "bad",
        "sample_model": "azure/m",
    }
    assert summary["cases"]["a"]["passed"]  # G1 not applicable is not a failure
    assert summary["aliases"]["assistant-reasoning"]["p95_ms"] == 500
    assert summary["tokens_in"] == 40
    markdown = render({"created_at": "t", "repeats": 2, "case_ids": ["a", "b"], "results": [summary]})
    assert "| b | ✗ G1 |" in markdown
    assert "`b` (1/2 repeats clean): G1: unsupported identifier: SO-10231" in markdown


def test_provider_refusals_are_counted_not_listed_as_models() -> None:
    refused = {**_run("assistant-fast", True, 50, "blocked"), "model_resolved": "assistant-fast (provider_refused)"}
    report = {
        "mode": "custom",
        "repeats": 2,
        "run_id": "r",
        "bindings": {},
        "canaries": {"ok": True},
        "decision": {"decision": "blocked", "reasons": []},
        "gates": {},
        "cases": [{"id": "a", "gates": {"G2": True}, "runs": [_run("assistant-fast", True, 100, "ok"), refused]}],
    }
    summary = summarize("m", {"profile": "x"}, report, 1)
    assert summary["aliases"]["assistant-fast"]["models"] == ["azure/m"]
    assert summary["aliases"]["assistant-fast"]["refusals"] == 1
    assert "(1 refused by content filter)" in render(
        {"created_at": "t", "repeats": 2, "case_ids": ["a"], "results": [summary]}
    )


def test_replaced_attempts_are_kept_as_history() -> None:
    old = {
        "name": "m",
        "run_id": "old",
        "decision": "harness_invalid",
        "gates": {"G1": {"passed": False}},
        "cases": {"b": {"passed": False, "notes": ["G1: x"]}, "a": {"passed": True, "notes": []}},
    }
    assert _brief(old) == {
        "run_id": "old",
        "decision": "harness_invalid",
        "gates": {"G1": False},
        "failed_cases": {"b": ["G1: x"]},
    }
