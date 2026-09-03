"""Replay transport cannot silently fall through to the live network."""

import json
import tempfile
import unittest
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from generate_composite_performance_fixture import generate as generate_composite
from nrw_events.benchmark import ReplayTransport, main, replay_differences, summarize


class BenchmarkTests(unittest.TestCase):
    def test_transport_returns_fresh_streams_and_content_type(self):
        transport = ReplayTransport({"https://example.test/feed": (b"calendar", "text/calendar", 200)})
        for _ in range(2):
            with transport.open(urllib.request.Request("https://example.test/feed")) as response:
                self.assertEqual(response.read(), b"calendar")
                self.assertIsInstance(response.headers, Message)
                self.assertEqual(response.headers.get_content_type(), "text/calendar")

    def test_missing_response_is_recorded_even_if_adapter_swallows_exception(self):
        transport = ReplayTransport({})
        with self.assertRaisesRegex(OSError, "missing replay response"):
            transport.open("https://example.test/missing")
        self.assertEqual(transport.misses, ["https://example.test/missing"])

    def test_failed_http_response_is_replayed(self):
        transport = ReplayTransport({"https://example.test/feed": (b"unavailable", "text/plain", 503)})
        with self.assertRaises(urllib.error.HTTPError) as raised:
            transport.open("https://example.test/feed")
        self.assertEqual(raised.exception.code, 503)
        raised.exception.close()

    def test_optional_latency_is_explicit_bounded_and_mockable(self):
        with self.assertRaises(ValueError):
            ReplayTransport({}, latency_ms=float("nan"))
        with self.assertRaises(ValueError):
            ReplayTransport({}, latency_ms=1001)
        transport = ReplayTransport({"https://example.test": (b"ok", "text/plain", 200)}, latency_ms=20)
        with patch("nrw_events.benchmark.time.sleep") as delay:
            with transport.open("https://example.test") as response:
                self.assertEqual(response.read(), b"ok")
        delay.assert_called_once_with(0.02)

    def test_percentiles_use_observed_nearest_rank_not_extrapolation(self):
        self.assertEqual(summarize([5, 1, 4, 2, 3]), {"min": 1, "median": 3, "p95": 5})
        with self.assertRaises(ValueError):
            summarize([])

    def test_full_pipeline_repeats_have_identical_semantics(self):
        manifest = Path(__file__).parent / "data" / "performance" / "manifest.json"
        with patch("sys.argv", ["benchmark", str(manifest), "--repetitions", "2"]), patch("builtins.print") as output:
            self.assertEqual(main(), 0)
        report = json.loads(output.call_args.args[0])
        self.assertEqual(report["semantic_differences"], [[]])
        first = report["runs"][0]
        self.assertGreater(first["snapshot"]["event_count"], 0)
        self.assertGreater(first["telemetry"]["stages"]["canonicalization.build_event"]["calls"], 0)

    def test_ledger_change_fails_even_when_public_snapshot_is_identical(self):
        left = {"snapshot": {}, "artifacts": {"series_ledger": {"series": {"a": {"announced_dates": []}}}}}
        right = {"snapshot": {}, "artifacts": {"series_ledger": {"series": {"a": {"announced_dates": ["2027-01-01"]}}}}}
        self.assertTrue(replay_differences(left, right))

    def test_real_composite_adapters_replay_cache_hits_and_misses(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = generate_composite(Path(temporary), per_calendar=2)
            with patch("sys.argv", ["benchmark", str(manifest), "--repetitions", "2"]), patch("builtins.print") as output:
                self.assertEqual(main(), 0)
            report = json.loads(output.call_args.args[0])
        first = report["runs"][0]
        self.assertEqual(report["semantic_differences"], [[]])
        self.assertEqual(first["snapshot"]["event_count"], 22)
        self.assertGreater(first["telemetry"]["counts"]["detail_cache_hits"], 0)
        self.assertGreater(first["telemetry"]["counts"]["detail_cache_misses"], 0)
        self.assertEqual(set(first["telemetry"]["sources"]), {"SiteKit regional", "ionas4 regional"})
        self.assertTrue(all(result["status"] == "healthy" for result in first["snapshot"]["source_results"].values()))


if __name__ == "__main__":
    unittest.main()
