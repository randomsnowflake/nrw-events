"""Opt-in, run-scoped diagnostics, separate from the published snapshot contract.

Stage times are inclusive: nested stages and overlapping workers must not be
summed to estimate elapsed import time. CPU measurements use thread_time, so
waiting for the GIL, network, or another worker is not attributed as CPU work.
Only the benchmark's outer boundary measures whole-process CPU and wall time.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


@dataclass
class _Stage:
    calls: int = 0
    wall_seconds: float = 0.0
    cpu_seconds: float = 0.0


class Collector:
    """Aggregate fixed-label metrics; never retain event text, URLs, or secrets."""

    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.perf_counter,
        cpu_clock: Callable[[], float] = time.thread_time,
    ) -> None:
        self.wall_clock = wall_clock
        self.cpu_clock = cpu_clock
        self._lock = threading.Lock()
        self._stages: dict[str, _Stage] = {}
        self._counts: dict[str, int] = {}

    def record(self, name: str, wall_seconds: float, cpu_seconds: float) -> None:
        with self._lock:
            stage = self._stages.setdefault(name, _Stage())
            stage.calls += 1
            stage.wall_seconds += wall_seconds
            stage.cpu_seconds += cpu_seconds

    def count(self, name: str, amount: int) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + amount

    def snapshot(self) -> dict:
        """Return detached JSON-ready data while late workers may still finish."""
        with self._lock:
            return {
                "schema_version": 1,
                "timing_semantics": "inclusive; concurrent and nested stages overlap",
                "stages": {
                    name: {
                        "calls": stage.calls,
                        "wall_ms": round(stage.wall_seconds * 1000, 6),
                        "thread_cpu_ms": round(stage.cpu_seconds * 1000, 6),
                    }
                    for name, stage in sorted(self._stages.items())
                },
                "counts": dict(sorted(self._counts.items())),
            }


_ACTIVE: ContextVar[Collector | None] = ContextVar("nrw_events_performance", default=None)


@contextmanager
def collect(collector: Collector) -> Iterator[Collector]:
    """Activate for this context; source copy_context workers share the collector."""
    token = _ACTIVE.set(collector)
    try:
        yield collector
    finally:
        _ACTIVE.reset(token)


@contextmanager
def span(name: str) -> Iterator[None]:
    collector = _ACTIVE.get()
    if collector is None:
        yield
        return
    wall_started = collector.wall_clock()
    cpu_started = collector.cpu_clock()
    try:
        yield
    finally:
        cpu_elapsed = collector.cpu_clock() - cpu_started
        wall_elapsed = collector.wall_clock() - wall_started
        collector.record(name, wall_elapsed, cpu_elapsed)


def count(name: str, amount: int = 1) -> None:
    collector = _ACTIVE.get()
    if collector is not None:
        collector.count(name, amount)


def queued_at() -> float | None:
    collector = _ACTIVE.get()
    return collector.wall_clock() if collector is not None else None


def record_queue_wait(started: float | None) -> None:
    collector = _ACTIVE.get()
    if collector is not None and started is not None:
        collector.record("source.queue_wait", collector.wall_clock() - started, 0.0)


def measured(name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Preserve callable metadata and avoid clock/lock work when disabled."""
    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if _ACTIVE.get() is None:
                return function(*args, **kwargs)
            with span(name):
                return function(*args, **kwargs)
        return wrapper
    return decorate
