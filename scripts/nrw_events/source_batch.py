"""Owning implementation of import orchestration; core is a compatibility facade."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context

from . import (
    common,
    components,
    performance,
)
from . import source_execution as _impl_source_execution
from .health import (
    SourceResult,
    SourceStatus,
)
from .import_phases import SourceBatch
from .models import CanonicalEvent, normalize_source_id
from .observability import log
from .runtime import RunContext
from .sources import SOURCE_IDS


def collect_sources(context: RunContext, sources: Mapping[str, Callable[[], object]],
                    executor_factory: Callable[..., ThreadPoolExecutor], import_started: float) -> SourceBatch:
    settings, logger, run_id = context.settings, context.logger, context.run_id
    log(logger, 20, f"fetching {len(sources)} sources", run_id=run_id, source="runner")
    all_events: list[CanonicalEvent] = []
    events_by_source: dict[str, list[CanonicalEvent]] = {}
    source_results: dict[str, SourceResult] = {}
    result: SourceResult | None
    worker_count = min(settings.source_workers, max(len(sources), 1))
    cache_warnings: list[dict[str, str]] = []
    pool = executor_factory(max_workers=worker_count)
    started: dict[str, tuple[float, threading.Thread, threading.Event, float]] = {}
    started_condition = threading.Condition()
    def run_source(name: str, fetch: Callable[[], object], queued_at: float | None) -> tuple[SourceResult, list[CanonicalEvent]]:
        cancel_event = threading.Event()
        source_timeout = settings.source_timeout_seconds
        # Network work stays capped by source_timeout_seconds.  The worker gets
        # a short grace period to canonicalize large successful payloads and
        # return partial detail enrichment instead of discarding the source.
        source_timeout += settings.source_processing_grace_seconds
        with started_condition:
            started[name] = (
                time.monotonic(), threading.current_thread(), cancel_event, source_timeout,
            )
            started_condition.notify_all()
        with performance.source_scope(name):
            performance.record_queue_wait(queued_at)
            return _impl_source_execution._run_source(name, fetch, settings.source_timeout_seconds, cancel_event)

    def accept_result(name: str, future: Future) -> None:
        result, events = future.result()
        source_results[name] = result
        if result.error:
            log(logger, 40, result.error["error"], run_id=run_id, source=name,
                error_type=result.error["error_type"])
        marker = "✓" if result.status in {
            SourceStatus.HEALTHY, SourceStatus.HEALTHY_EMPTY,
            SourceStatus.SCHEDULED_SKIP, SourceStatus.DISABLED,
        } else "!"
        log(logger, 20 if marker == "✓" else 30,
            f"{marker} {result.status.value}: {result.accepted_event_count}/{result.raw_event_count} events in {result.duration_ms}ms",
            run_id=run_id, source=name)
        events_by_source[name] = events

    try:
        futures = {
            pool.submit(
                copy_context().run,
                run_source,
                name,
                fetch,
                performance.queued_at(),
            ): name
            for name, fetch in sources.items()
        }
        pending = set(futures)
        while pending:
            unstarted_names = {
                futures[future] for future in pending
                if futures[future] not in started
            }
            if unstarted_names:
                def any_started(names: set[str] = unstarted_names) -> bool:
                    return any(name in started for name in names)
                with started_condition:
                    started_condition.wait_for(
                        any_started,
                        timeout=0.05,
                    )
            now = time.monotonic()
            pending_deadlines = [
                started[name][0] + started[name][3] - now
                for future in pending
                if (name := futures[future]) in started
            ]
            # A queued future has no start timestamp yet. Recheck it promptly
            # without returning to the former 10 ms busy-poll cadence.
            next_deadline = (
                0.05
                if len(pending_deadlines) < len(pending)
                else min(pending_deadlines, default=1.0)
            )
            wait_timeout = max(0.05, min(next_deadline, 1.0))
            completed, _ = wait(
                pending, timeout=wait_timeout, return_when=FIRST_COMPLETED
            )
            for future in completed:
                name = futures[future]
                if (
                    name in started
                    and time.monotonic() - started[name][0] >= started[name][3]
                    and future.result()[0].duration_ms > started[name][3] * 1000
                ):
                    continue
                pending.remove(future)
                accept_result(name, future)

            now = time.monotonic()
            timed_out = [
                future for future in pending
                if futures[future] in started
                and now - started[futures[future]][0] >= started[futures[future]][3]
            ]
            for future in timed_out:
                pending.remove(future)
                name = futures[future]
                if (
                    future.done()
                    and future.result()[0].duration_ms <= started[name][3] * 1000
                ):
                    accept_result(name, future)
                    continue
                worker = started[name][1]
                started[name][2].set()
                future.cancel()
                replace_worker = getattr(pool, "replace_stalled_worker", None)
                if replace_worker is not None:
                    replace_worker(worker)
                result = SourceResult(
                    source=name,
                    source_id=SOURCE_IDS.get(name, normalize_source_id(name)),
                )
                result.error = {
                    "error_type": "TimeoutError",
                    "error": f"source exceeded {started[name][3]:g}s wall-clock budget",
                }
                result.duration_ms = round(started[name][3] * 1000)
                result.finish([])
                source_results[name] = result
                log(
                    logger, 40, result.error["error"], run_id=run_id, source=name,
                    error_type=result.error["error_type"],
                )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        # Source workers share the detail-cache lock. Persist dirty namespaces
        # once after all workers finish instead of serializing every source at
        # its boundary while other workers still need cache lookups.
        if components.pending():
            log(logger, 30, "cache flush deferred until component workers finish", run_id=run_id, source="runner")
        else:
            cache_warnings.extend(common.flush_detail_page_caches())
    source_results = {
        name: source_results[name]
        for name in sources
        if name in source_results
    }
    all_events = [event for name in sources for event in events_by_source.get(name, [])]
    source_import_duration_ms = round((time.monotonic() - import_started) * 1000)
    return SourceBatch(all_events, source_results, cache_warnings, source_import_duration_ms)
