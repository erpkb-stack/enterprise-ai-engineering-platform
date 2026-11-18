"""Distributed-systems primitives: circuit breaker, retry with jittered backoff,
bounded concurrency with backpressure, and timeout helpers.

These are deliberately dependency-free so they can be unit tested deterministically.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

from app.core.errors import CircuitOpenError, OverloadedError

T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Classic three-state breaker. Opens after `failure_threshold` consecutive failures,
    stays open for `reset_timeout_s`, then admits a single probe (half-open)."""

    name: str
    failure_threshold: int = 5
    reset_timeout_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    total_failures: int = 0
    total_successes: int = 0
    _probe_in_flight: bool = False

    def _maybe_half_open(self) -> None:
        if self.state is CircuitState.OPEN and self.clock() - self.opened_at >= self.reset_timeout_s:
            self.state = CircuitState.HALF_OPEN
            self._probe_in_flight = False

    def allow(self) -> bool:
        self._maybe_half_open()
        if self.state is CircuitState.CLOSED:
            return True
        if self.state is CircuitState.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self.total_successes += 1
        self.failures = 0
        self.state = CircuitState.CLOSED
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self.total_failures += 1
        self.failures += 1
        if self.state is CircuitState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = self.clock()
            self._probe_in_flight = False

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        if not self.allow():
            raise CircuitOpenError(f"Circuit '{self.name}' is open", details={"breaker": self.name})
        try:
            result = await fn()
        except asyncio.CancelledError:
            self._probe_in_flight = False
            raise
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def snapshot(self) -> dict:
        self._maybe_half_open()
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self.failures,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
        }


class BreakerRegistry:
    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str, **kw: float) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name=name, **kw)  # type: ignore[arg-type]
        return self._breakers[name]

    def all(self) -> list[dict]:
        return [b.snapshot() for b in self._breakers.values()]


breakers = BreakerRegistry()


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.2
    max_delay_s: float = 5.0
    retry_on: tuple[type[BaseException], ...] = (Exception,)
    no_retry_on: tuple[type[BaseException], ...] = ()

    def delay(self, attempt: int) -> float:
        # full jitter exponential backoff (AWS architecture blog)
        return random.uniform(0, min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1))))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except policy.no_retry_on:
            raise
        except policy.retry_on as exc:
            if attempt >= policy.max_attempts:
                raise
            if on_retry:
                on_retry(attempt, exc)
            await asyncio.sleep(policy.delay(attempt))


@dataclass
class BoundedExecutor:
    """Bounds in-flight work with a semaphore and rejects (backpressure) when the wait queue is full."""

    max_concurrency: int
    max_queue: int
    _sem: asyncio.Semaphore = field(init=False)
    _waiting: int = 0
    _active: int = 0
    rejected: int = 0

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.max_concurrency)

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return self._waiting

    async def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        if self._sem.locked() and self._waiting >= self.max_queue:
            self.rejected += 1
            raise OverloadedError(
                "Execution capacity exhausted; retry later",
                details={"active": self._active, "queued": self._waiting},
            )
        self._waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self._waiting -= 1
        self._active += 1
        try:
            return await fn()
        finally:
            self._active -= 1
            self._sem.release()

    def snapshot(self) -> dict:
        return {
            "max_concurrency": self.max_concurrency,
            "max_queue": self.max_queue,
            "active": self._active,
            "queued": self._waiting,
            "rejected_total": self.rejected,
        }


async def with_timeout(coro: Awaitable[T], seconds: float, what: str = "operation") -> T:
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except TimeoutError as exc:
        raise TimeoutError(f"{what} timed out after {seconds:.1f}s") from exc
