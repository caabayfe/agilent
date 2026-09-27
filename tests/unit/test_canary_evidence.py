from evals.gates import CaseResult, RunRecord


def _run(evidence: str, tool_errors: int) -> RunRecord:
    return RunRecord("t", "a", "assistant-fast", "m", ["get_service_history"], 1, {}, evidence, tool_errors=tool_errors)


def test_canary_reference_skips_repeats_whose_tools_failed() -> None:
    result = CaseResult({"id": "c"}, [_run('{"error": "unavailable"}', 1), _run("visit 2026-01-02", 0)])
    assert result.reference_evidence() == "visit 2026-01-02"


def test_canary_reference_falls_back_to_first_repeat() -> None:
    assert CaseResult({"id": "c"}, [_run("denied", 1), _run("denied", 1)]).reference_evidence() == "denied"
    assert CaseResult({"id": "c"}).reference_evidence() == ""
