"""One bounded, run-scoped pool for independent calendars inside source groups.

Only the source thread merges observations. Workers inherit absolute deadlines,
but never share the mutable SourceResult or parser counters of their parent.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import core, performance
from .health import SourceResult, SourceStatus


@dataclass(frozen=True)
class Job:
    url: str
    fetch: Callable[[], list]


@dataclass
class _Pool:
    executor: ThreadPoolExecutor
    futures: list[Future] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


_ACTIVE: ContextVar[_Pool | None] = ContextVar("nrw_component_pool", default=None)
_IN_WORKER: ContextVar[bool] = ContextVar("nrw_component_worker", default=False)
COMPOSITE_SOURCES = frozenset({
    "SiteKit regional", "ionas4 regional", "Regional HTML calendars",
    "Requested venue calendars", "Bonn venue calendars",
})


@contextmanager
def pool_scope(workers: int, *, executor_factory: Callable = ThreadPoolExecutor) -> Iterator[None]:
    """Share one pool across source threads; never create nested worker pools."""
    pool = _Pool(executor_factory(max_workers=min(workers, 4))) if workers > 1 else None
    token = _ACTIVE.set(pool)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
        if pool is not None:
            pool.executor.shutdown(wait=False, cancel_futures=True)


def pending() -> bool:
    """Detect late workers before the parent persists shared cache namespaces."""
    pool = _ACTIVE.get()
    if pool is None:
        return False
    with pool.lock:
        return any(not future.done() for future in pool.futures)


def enabled() -> bool:
    return _ACTIVE.get() is not None and not _IN_WORKER.get()


def _check_budget(state: dict, *, completion: bool = False) -> None:
    cancel = state.get("cancel_event")
    deadline = state.get("hard_deadline")
    if completion and deadline is not None:
        deadline += core._runtime_state().settings.source_processing_grace_seconds
    if (cancel is not None and cancel.is_set()) or (deadline is not None and time.perf_counter() >= deadline):
        raise TimeoutError("source time budget exhausted before component completion")


def _merge(parent: SourceResult, child: SourceResult) -> None:
    for warning in child.warnings:
        if warning not in parent.warnings:
            parent.warnings.append(warning)
    for url, observation in child.endpoints.items():
        attempts = parent.endpoints.get(url, {}).get("attempts", 0)
        parent.endpoint(url, **{key: value for key, value in observation.items() if key != "attempts"})
        parent.endpoints[url]["attempts"] = attempts + observation.get("attempts", 0)
    parent.rejected_event_count += child.rejected_event_count
    parent.detail_deadline_skipped_event_count += child.detail_deadline_skipped_event_count
    for reason, count in child.rejection_reasons.items():
        parent.rejection_reasons[reason] = parent.rejection_reasons.get(reason, 0) + count
    for reason, sample in child.rejection_samples.items():
        parent.rejection_samples.setdefault(reason, sample)
    parent.cancelled_events.extend(child.cancelled_events)
    parent.announced_events.extend(child.announced_events)
    parent._ai_source_material.extend(child._ai_source_material)
    if child.status != SourceStatus.HEALTHY_EMPTY:
        parent.status = child.status


def _invoke(job: Job, state: dict) -> tuple[list, SourceResult | None, dict]:
    # The pool reuses threads. Restore every pre-existing attribute, including
    # absent attributes, so deadlines and parser counters cannot leak.
    old_state = vars(core._SOURCE_CONTEXT).copy()
    child_state = {key: value for key, value in state.items() if key not in {"result", "parser_metrics"}}
    parent = state.get("result")
    child = SourceResult(parent.source, source_id=parent.source_id) if parent else None
    child_state["result"] = child
    core._SOURCE_CONTEXT.__dict__.clear()
    core._SOURCE_CONTEXT.__dict__.update(child_state)
    token = _IN_WORKER.set(True)
    try:
        _check_budget(state)
        with core.capture_parser_metrics() as metrics, performance.span("source.component"):
            events = job.fetch()
        return events, child, metrics
    finally:
        _IN_WORKER.reset(token)
        core._SOURCE_CONTEXT.__dict__.clear()
        core._SOURCE_CONTEXT.__dict__.update(old_state)


def _group(jobs: list[tuple[int, Job]], state: dict) -> list[tuple[int, tuple]]:
    return [(index, _invoke(job, state)) for index, job in jobs]


def run(jobs: Sequence[Job]) -> list:
    """Fetch independent host groups concurrently and flatten in registry order."""
    pool = _ACTIVE.get()
    state = vars(core._SOURCE_CONTEXT).copy()
    if pool is None or _IN_WORKER.get():
        events = []
        for job in jobs:
            _check_budget(state)
            events.extend(job.fetch())
        return events
    groups: dict[str, list[tuple[int, Job]]] = {}
    for index, job in enumerate(jobs):
        bucket, _delay = core._throttle_bucket(job.url)
        host = bucket or (urlsplit(job.url).hostname or job.url).lower()
        groups.setdefault(host, []).append((index, job))
    _check_budget(state)
    futures = []
    for group in groups.values():
        future = pool.executor.submit(copy_context().run, _group, group, state)
        futures.append(future)
        with pool.lock:
            pool.futures.append(future)
    completed: dict[int, tuple] = {}
    try:
        # Polling here checks the original source budget, never a renewed one.
        for future in futures:
            while not future.done():
                _check_budget(state, completion=True)
                wait((future,), timeout=0.05)
            completed.update(future.result())
        _check_budget(state, completion=True)
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    events = []
    for index in range(len(jobs)):
        rows, child, metrics = completed[index]
        events.extend(rows)
        if child is not None:
            _merge(state["result"], child)
        if "parser_metrics" in state:
            for key, count in metrics.items():
                state["parser_metrics"][key] += count
    return events
