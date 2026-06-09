import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from scripts.nrw_events import common, runner


class RunnerOutputTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 6, 8)
        common.END_DATE = datetime(2026, 6, 10)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

    def test_default_json_output_preserves_top_level_event_list(self):
        def fetch_event():
            return [{
                "title": "Concert",
                "date": common.TODAY.strftime("%Y-%m-%d"),
                "time": "20:00",
                "venue": "Club",
                "city": "Bonn",
                "description": "",
                "price": "",
                "link": "https://example.test",
                "distance_km": 0,
                "score": 1.0,
                "source": "Test",
                "category": "konzert",
            }]

        with tempfile.TemporaryDirectory() as tmpdir:
            json_out = os.path.join(tmpdir, "events.json")
            meta_out = os.path.join(tmpdir, "events-meta.json")
            with mock.patch.dict(os.environ, {
                "NRW_EVENTS_JSON_OUT": json_out,
                "NRW_EVENTS_META_JSON_OUT": meta_out,
            }, clear=False):
                with mock.patch.object(runner, "SOURCES", {"Test": fetch_event}):
                    with mock.patch.object(runner.report, "format_report", lambda events: ""):
                        with mock.patch.object(sys, "argv", ["runner"]):
                            runner.main()

            with open(json_out) as f:
                events_payload = json.load(f)
            with open(meta_out) as f:
                meta_payload = json.load(f)

        self.assertIsInstance(events_payload, list)
        self.assertEqual(events_payload[0]["title"], "Concert")
        self.assertIsInstance(meta_payload, dict)
        self.assertEqual(meta_payload["events"][0]["title"], "Concert")
        self.assertEqual(meta_payload["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
