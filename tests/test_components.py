"""Bounded component scheduling preserves source context and registry order."""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from unittest.mock import patch

from nrw_events import components, core, performance, runner
from nrw_events.health import SourceResult

from .helpers import make_runner_env


class ComponentTests(unittest.TestCase):
    def tearDown(self):
        core.set_source_context(None)

    def test_independent_hosts_overlap_but_results_and_diagnostics_keep_order(self):
        barrier = threading.Barrier(2)
        parent = SourceResult("Fixture")
        core.set_source_context(parent, 30)
        deadline = core._SOURCE_CONTEXT.hard_deadline
        collector = performance.Collector()

        def job(index):
            self.assertIsNot(core._SOURCE_CONTEXT.result, parent)
            self.assertEqual(core._SOURCE_CONTEXT.hard_deadline, deadline)
            barrier.wait(timeout=2)
            core.log_source_error(f"child-{index}", ValueError("fixture"), source_id=f"child-{index}")
            core._record_parser_candidate(out_of_window=True)
            return [index]

        with performance.collect(collector), core.capture_parser_metrics() as metrics:
            with components.pool_scope(2, executor_factory=ThreadPoolExecutor):
                result = components.run([
                    components.Job("https://a.test", lambda: job(0)),
                    components.Job("https://b.test", lambda: job(1)),
                ])
        self.assertEqual(result, [0, 1])
        self.assertEqual([item["source_id"] for item in parent.warnings], ["child-0", "child-1"])
        self.assertEqual(metrics, {"candidate_count": 2, "out_of_window_count": 2})
        self.assertEqual(collector.snapshot()["counts"]["parser_candidates"], 2)

    def test_same_host_is_serial_and_nested_run_does_not_deadlock(self):
        visits = []

        def job(index):
            visits.append(index)
            return components.run([components.Job("https://nested.test", lambda: [index])])

        with components.pool_scope(2, executor_factory=ThreadPoolExecutor):
            result = components.run([
                components.Job("https://a.test/one", lambda: job(0)),
                components.Job("https://a.test/two", lambda: job(1)),
            ])
        self.assertEqual(result, [0, 1])
        self.assertEqual(visits, [0, 1])

    def test_cancelled_queue_does_not_invoke_fetchers(self):
        cancel = threading.Event()
        cancel.set()
        core.set_source_context(SourceResult("Fixture"), 30, cancel)
        with components.pool_scope(2, executor_factory=ThreadPoolExecutor):
            with patch.object(self, "fail") as fetch, self.assertRaises(TimeoutError):
                components.run([components.Job("https://a.test", fetch)])
            fetch.assert_not_called()

    def test_serial_fallback_keeps_original_context(self):
        parent = SourceResult("Fixture")
        core.set_source_context(parent, 30)

        def fetch():
            self.assertIs(core._SOURCE_CONTEXT.result, parent)
            return [1]

        self.assertEqual(components.run([components.Job("https://a.test", fetch)]), [1])

    def test_multiple_sources_share_the_global_limit(self):
        release = threading.Event()
        started = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0

        def fetch():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    started.set()
            try:
                self.assertTrue(release.wait(timeout=3))
                return [1]
            finally:
                with lock:
                    active -= 1

        with components.pool_scope(2, executor_factory=ThreadPoolExecutor), ThreadPoolExecutor(2) as sources:
            futures = [sources.submit(copy_context().run, components.run, [
                components.Job(f"https://{source}-{index}.test", fetch) for index in range(3)
            ]) for source in range(2)]
            try:
                self.assertTrue(started.wait(timeout=2))
                self.assertTrue(components.pending())
                self.assertEqual(peak, 2)
            finally:
                release.set()
            self.assertEqual([future.result(timeout=3) for future in futures], [[1, 1, 1], [1, 1, 1]])
            self.assertFalse(components.pending())
        self.assertEqual(peak, 2)

    def test_bonn_subdomains_share_one_ordered_bucket(self):
        visits = []
        with patch.object(core, "_throttle_bucket", return_value=("bonn.de", 0.5)):
            with components.pool_scope(3, executor_factory=ThreadPoolExecutor):
                components.run([
                    components.Job(f"https://{index}.bonn.de", lambda index=index: visits.append(index) or [])
                    for index in range(5)
                ])
        self.assertEqual(visits, list(range(5)))

    def test_deadline_is_not_reset_and_worker_state_is_restored(self):
        parent = SourceResult("Fixture")
        core.set_source_context(parent, 30)
        saved = vars(core._SOURCE_CONTEXT).copy()
        with patch.object(components.time, "perf_counter", return_value=saved["hard_deadline"] + 1):
            with components.pool_scope(2, executor_factory=ThreadPoolExecutor):
                with self.assertRaises(TimeoutError):
                    components.run([components.Job("https://a.test", lambda: self.fail("expired work ran"))])
        with self.assertRaisesRegex(ValueError, "failure"):
            components._invoke(components.Job("https://a.test", lambda: (_ for _ in ()).throw(ValueError("failure"))), saved)
        self.assertEqual(vars(core._SOURCE_CONTEXT), saved)

    def test_diagnostics_merge_matches_serial_updates(self):
        parent = SourceResult("Fixture")
        core.set_source_context(parent, 30)

        def fetch(index):
            core._record_endpoint("https://shared.test", status=200, duration_ms=index)
            core._SOURCE_CONTEXT.result.reject("fixture", {"title": str(index)})
            core._SOURCE_CONTEXT.result.cancelled_events.append({"title": str(index)})
            return [index]

        with components.pool_scope(2, executor_factory=ThreadPoolExecutor):
            components.run([components.Job(f"https://{index}.test", lambda index=index: fetch(index)) for index in range(2)])
        self.assertEqual(parent.endpoints["https://shared.test"]["attempts"], 2)
        self.assertEqual(parent.endpoints["https://shared.test"]["duration_ms"], 1)
        self.assertEqual(parent.rejection_reasons, {"fixture": 2})
        self.assertEqual(parent.rejection_samples["fixture"]["title"], "0")
        self.assertEqual(parent.cancelled_events, [{"title": "0"}, {"title": "1"}])

    def test_cancellation_stops_already_queued_work(self):
        started = threading.Barrier(3)
        release = threading.Event()
        cancel = threading.Event()
        core.set_source_context(SourceResult("Fixture"), 30, cancel)
        queued_called = threading.Event()

        def blocking():
            started.wait(timeout=3)
            self.assertTrue(release.wait(timeout=3))
            return []

        def run_source():
            # Like the real source runner, establish thread-local context in
            # the source thread. copy_context copies ContextVars only.
            core.set_source_context(SourceResult("Fixture"), 30, cancel)
            try:
                return components.run([
                    components.Job("https://a.test", blocking),
                    components.Job("https://b.test", blocking),
                    components.Job("https://c.test", lambda: queued_called.set() or []),
                ])
            finally:
                core.set_source_context(None)

        with components.pool_scope(2, executor_factory=ThreadPoolExecutor), ThreadPoolExecutor(1) as source:
            future = source.submit(copy_context().run, run_source)
            try:
                started.wait(timeout=3)
                cancel.set()
            finally:
                release.set()
            with self.assertRaises(TimeoutError):
                future.result(timeout=3)
        self.assertFalse(queued_called.is_set())

    def test_cache_flush_occurs_once_after_components_finish(self):
        old_window = core.TODAY, core.END_DATE, core.DAYS_AHEAD
        finished = []

        def fetch(index):
            if index == 0:
                core.log_source_error("child-0", OSError("optional child failed"), source_id="child-0")
            finished.append(index)
            return []

        def flush():
            self.assertEqual(sorted(finished), [0, 1])
            self.assertFalse(components.pending())
            return []

        try:
            with make_runner_env() as environment, patch.object(core, "flush_detail_page_caches", side_effect=flush) as persist:
                result = runner.run_import(environment.context(series_ledger_json=""), {"Fixture": lambda: components.run([
                    components.Job(f"https://{index}.test", lambda index=index: fetch(index)) for index in range(2)
                ])})
            persist.assert_called_once_with()
            self.assertEqual(result.source_results["Fixture"].warnings[0]["source_id"], "child-0")
        finally:
            core.TODAY, core.END_DATE, core.DAYS_AHEAD = old_window
            core._configure_date_reference(old_window[0])


if __name__ == "__main__":
    unittest.main()
