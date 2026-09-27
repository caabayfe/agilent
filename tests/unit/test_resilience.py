"""MCP client resilience: timeouts, jittered retry of transient errors, circuit breaker."""

import asyncio

import httpx
import pytest

from cfa.agent.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    Resilience,
    RetryPolicy,
    is_transient,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def resilience(**kwargs: object) -> tuple[Resilience, list[float]]:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return Resilience(sleep=sleep, rng=lambda: 1.0, **kwargs), slept  # type: ignore[arg-type]


class Flaky:
    """Fails ``failures`` times with ``error``, then returns "ok"."""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures, self.calls = failures, 0
        self.error = error or httpx.ConnectError("refused")

    async def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def test_full_jitter_is_bounded_by_the_exponential_cap() -> None:
    policy = RetryPolicy(base_s=0.2, cap_s=2.0)
    assert [policy.backoff(n, rng=lambda: 1.0) for n in range(6)] == [0.2, 0.4, 0.8, 1.6, 2.0, 2.0]
    assert policy.backoff(3, rng=lambda: 0.0) == 0.0  # full jitter: anywhere in [0, cap]


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (httpx.ConnectError("refused"), True),
        (httpx.ReadTimeout("slow"), True),
        (TimeoutError(), True),
        (ExceptionGroup("mcp", [httpx.ConnectError("refused")]), True),
        (httpx.HTTPStatusError("503", request=httpx.Request("POST", "http://x"), response=httpx.Response(503)), True),
        (httpx.HTTPStatusError("401", request=httpx.Request("POST", "http://x"), response=httpx.Response(401)), False),
        (ValueError("bad payload"), False),
    ],
)
def test_only_transport_and_server_failures_are_transient(exc: Exception, transient: bool) -> None:
    assert is_transient(exc) is transient


async def test_transient_failures_are_retried_with_backoff() -> None:
    r, slept = resilience()
    op = Flaky(failures=2)
    assert await r.run("billing", op) == ("ok", 3)
    assert slept == [0.2, 0.4]
    assert r.breaker("billing").state is CircuitState.CLOSED


async def test_non_transient_errors_are_not_retried() -> None:
    r, slept = resilience()
    op = Flaky(failures=5, error=ValueError("bad"))
    with pytest.raises(ValueError, match="bad"):
        await r.run("billing", op)
    assert (op.calls, slept) == (1, [])


async def test_a_hung_call_times_out_and_counts_as_a_failure() -> None:
    r, _ = resilience(attempt_timeout_s=0.01, retry=RetryPolicy(attempts=1))

    async def hang() -> str:
        await asyncio.sleep(10)
        return "never"

    with pytest.raises(TimeoutError):
        await r.run("orders", hang)
    assert r.breaker("orders").failures == 1


async def test_breaker_opens_then_fails_fast_without_calling_the_server() -> None:
    r, _ = resilience(failure_threshold=3)
    down = Flaky(failures=100)
    with pytest.raises(httpx.ConnectError):
        await r.run("billing", down)  # 3 attempts -> 3 failures -> open
    assert r.breaker("billing").state is CircuitState.OPEN
    calls = down.calls
    with pytest.raises(CircuitOpenError):
        await r.run("billing", down)
    assert down.calls == calls  # failed fast


async def test_one_sick_skill_does_not_open_the_others() -> None:
    r, _ = resilience(failure_threshold=1, retry=RetryPolicy(attempts=1))
    with pytest.raises(httpx.ConnectError):
        await r.run("billing", Flaky(failures=1))
    assert await r.run("orders", Flaky(failures=0)) == ("ok", 1)


def state(breaker: CircuitBreaker) -> CircuitState:
    return breaker.state  # read through a call so mypy does not narrow across transitions


def test_half_open_allows_one_trial_then_closes_or_reopens() -> None:
    clock = Clock()
    breaker = CircuitBreaker(failure_threshold=2, reset_after_s=10, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.allow()
    clock.now = 10
    assert breaker.allow()  # the single trial call
    assert not breaker.allow()  # nobody else while the trial is in flight
    breaker.record_failure()  # trial failed -> open again, timer restarted
    assert state(breaker) is CircuitState.OPEN
    clock.now = 20
    assert breaker.allow()
    breaker.record_success()
    assert state(breaker) is CircuitState.CLOSED
    assert breaker.allow()
