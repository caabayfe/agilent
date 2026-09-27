"""Client-side resilience for calls to the skill server.

* **Timeout** per attempt, so a hung server cannot stall a customer conversation.
* **Retry** with exponential backoff and *full jitter* (sleep ~ U(0, min(cap, base*2^n)))
  so many agents recovering at once do not stampede the server. Only transient
  transport failures are retried, and only because every skill tool is read-only
  (``readOnlyHint``); a write tool would need an idempotency key first.
* **Circuit breaker** per skill (closed -> open -> half-open): after N consecutive
  transient failures calls fail fast for ``reset_after_s``, then one trial call
  decides whether to close again. One sick skill does not slow the others down.

Denials, not-found and validation errors are *answers*, not failures: they never
trip the breaker and are never retried.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

log = logging.getLogger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised without calling the server while the breaker is open."""


@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = 3
    reset_after_s: float = 15.0
    clock: Callable[[], float] = time.monotonic
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    _opened_at: float = 0.0
    _trial_in_flight: bool = False

    def allow(self) -> bool:
        if self.state is CircuitState.OPEN and self.clock() - self._opened_at >= self.reset_after_s:
            self.state, self._trial_in_flight = CircuitState.HALF_OPEN, False
        if self.state is CircuitState.HALF_OPEN:
            if self._trial_in_flight:
                return False
            self._trial_in_flight = True
            return True
        return self.state is CircuitState.CLOSED

    def record_success(self) -> None:
        self.state, self.failures, self._trial_in_flight = CircuitState.CLOSED, 0, False

    def record_failure(self) -> None:
        self.failures += 1
        if self.state is CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state, self._opened_at, self._trial_in_flight = CircuitState.OPEN, self.clock(), False


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    base_s: float = 0.2
    cap_s: float = 2.0

    def backoff(self, attempt: int, rng: Callable[[], float] = random.random) -> float:
        """Full jitter for retry number ``attempt`` (0-based)."""
        return rng() * min(self.cap_s, self.base_s * 2.0**attempt)


def is_transient(exc: BaseException) -> bool:
    """Worth retrying: the server was unreachable, slow or temporarily failing."""
    if isinstance(exc, BaseExceptionGroup):
        return any(is_transient(e) for e in exc.exceptions)
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.TransportError | TimeoutError | OSError)


@dataclass(slots=True)
class Resilience:
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    attempt_timeout_s: float = 5.0
    failure_threshold: int = 3
    reset_after_s: float = 15.0
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    rng: Callable[[], float] = random.random
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def breaker(self, key: str) -> CircuitBreaker:
        if key not in self.breakers:
            self.breakers[key] = CircuitBreaker(self.failure_threshold, self.reset_after_s)
        return self.breakers[key]

    async def run[T](self, key: str, operation: Callable[[], Awaitable[T]]) -> tuple[T, int]:
        """Run ``operation`` under the breaker for ``key``; returns (result, attempts used).

        Raises ``CircuitOpenError`` when failing fast, or the last error when a
        non-transient error occurs or retries are exhausted.
        """
        breaker = self.breaker(key)
        for attempt in range(self.retry.attempts):
            if not breaker.allow():
                raise CircuitOpenError(key)
            try:
                async with asyncio.timeout(self.attempt_timeout_s):
                    result = await operation()
            except Exception as exc:
                if not is_transient(exc):
                    breaker.record_success()  # the server answered; the request itself was bad
                    raise
                breaker.record_failure()
                last_attempt = attempt == self.retry.attempts - 1
                log.warning(
                    "skill call %s failed (%s), attempt %d/%d, circuit %s",
                    key,
                    type(exc).__name__,
                    attempt + 1,
                    self.retry.attempts,
                    breaker.state,
                )
                if last_attempt or breaker.state is CircuitState.OPEN:
                    raise
                await self.sleep(self.retry.backoff(attempt, self.rng))
            else:
                breaker.record_success()
                return result, attempt + 1
        raise AssertionError("unreachable")  # pragma: no cover
