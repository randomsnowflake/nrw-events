"""Performance diagnostics must not alter imports or hide semantic changes."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from nrw_events import performance


class PerformanceTests(unittest.TestCase):
    def test_disabled_measurement_preserves_results_and_errors(self):
        @performance.measured("operation")
        def operation(value):
            if value is None:
                raise ValueError("unchanged")
            return value

        value = object()
        self.assertIs(operation(value), value)
        with self.assertRaisesRegex(ValueError, "unchanged"):
            operation(None)

    def test_fake_clocks_measure_wall_and_thread_cpu_even_on_error(self):
        wall = iter([10.0, 13.0])
        cpu = iter([2.0, 2.5])
        collector = performance.Collector(wall_clock=lambda: next(wall), cpu_clock=lambda: next(cpu))
        with performance.collect(collector):
            with self.assertRaises(ValueError), performance.span("parse"):
                performance.count("candidates", 3)
                raise ValueError("bad payload")
        result = collector.snapshot()
        self.assertEqual(result["stages"]["parse"], {
            "calls": 1, "wall_ms": 3000.0, "thread_cpu_ms": 500.0,
        })
        self.assertEqual(result["counts"], {"candidates": 3})

    def test_nested_collections_restore_outer_context(self):
        outer = performance.Collector()
        inner = performance.Collector()
        with performance.collect(outer):
            performance.count("outer")
            with performance.collect(inner):
                performance.count("inner")
            performance.count("outer")
        performance.count("disabled")
        self.assertEqual(outer.snapshot()["counts"], {"outer": 2})
        self.assertEqual(inner.snapshot()["counts"], {"inner": 1})

    def test_worker_context_shares_collector_and_updates_are_not_lost(self):
        collector = performance.Collector()

        @performance.measured("worker")
        def worker():
            for _ in range(100):
                performance.count("records")

        with performance.collect(collector), ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(copy_context().run, worker) for _ in range(20)]
            for future in futures:
                future.result()
        self.assertEqual(collector.snapshot()["counts"]["records"], 2000)
        self.assertEqual(collector.snapshot()["stages"]["worker"]["calls"], 20)

    def test_snapshot_is_detached_and_sorted(self):
        collector = performance.Collector()
        with performance.collect(collector):
            performance.count("z")
            performance.count("a")
        snapshot = collector.snapshot()
        self.assertEqual(list(snapshot["counts"]), ["a", "z"])
        snapshot["counts"]["z"] = 99
        self.assertEqual(collector.snapshot()["counts"]["z"], 1)


if __name__ == "__main__":
    unittest.main()
