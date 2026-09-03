"""Replay transport cannot silently fall through to the live network."""

import json
import unittest
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from nrw_events.benchmark import ReplayTransport, main, summarize


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


if __name__ == "__main__":
    unittest.main()
