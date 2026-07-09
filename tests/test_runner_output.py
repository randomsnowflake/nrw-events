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
        self.assertEqual(meta_payload["events"][0]["category_key"], "concert")
        self.assertEqual(meta_payload["events"][0]["category_label"], "Konzert")
        self.assertGreater(meta_payload["events"][0]["category_confidence"], 0)
        self.assertIn("concert", meta_payload["events"][0]["category_reason"])
        self.assertGreaterEqual(len(meta_payload["categories"]), 12)
        self.assertIn({"key": "concert", "label": "Konzert"}, meta_payload["categories"])
        self.assertEqual(meta_payload["event_count"], 1)

    def test_metadata_includes_source_warnings_from_swallowed_source_errors(self):
        def fetch_with_warning():
            runner.common.log_source_error("Fragile Source", RuntimeError("layout changed"))
            return []

        with tempfile.TemporaryDirectory() as tmpdir:
            json_out = os.path.join(tmpdir, "events.json")
            meta_out = os.path.join(tmpdir, "events-meta.json")
            with mock.patch.dict(os.environ, {
                "NRW_EVENTS_JSON_OUT": json_out,
                "NRW_EVENTS_META_JSON_OUT": meta_out,
            }, clear=False):
                with mock.patch.object(runner, "SOURCES", {"Fragile Source": fetch_with_warning}):
                    with mock.patch.object(runner.report, "format_report", lambda events: ""):
                        with mock.patch.object(sys, "argv", ["runner"]):
                            runner.main()

            with open(meta_out) as f:
                meta_payload = json.load(f)

        self.assertEqual(
            meta_payload["source_warnings"],
            [{"source": "Fragile Source", "error_type": "RuntimeError", "error": "layout changed"}],
        )
        self.assertEqual(meta_payload["run_status"], "degraded")
        self.assertEqual(meta_payload["source_results"]["Fragile Source"]["status"], "degraded")

    def test_critical_source_failure_preserves_existing_snapshot(self):
        def broken_fetch():
            raise RuntimeError("upstream unavailable")

        with tempfile.TemporaryDirectory() as tmpdir:
            json_out = os.path.join(tmpdir, "events.json")
            meta_out = os.path.join(tmpdir, "events-meta.json")
            with open(json_out, "w") as handle:
                handle.write('["last-known-good"]')
            with open(meta_out, "w") as handle:
                handle.write('{"last-known-good": true}')
            with mock.patch.dict(os.environ, {
                "NRW_EVENTS_JSON_OUT": json_out,
                "NRW_EVENTS_META_JSON_OUT": meta_out,
            }, clear=False), mock.patch.object(runner, "SOURCES", {"Bonn.de Events": broken_fetch}), \
                    mock.patch.object(runner.report, "format_report", lambda events: ""), \
                    mock.patch.object(sys, "argv", ["runner"]):
                self.assertEqual(runner.main(), runner.EXIT_FAILED)

            with open(json_out) as handle:
                self.assertEqual(handle.read(), '["last-known-good"]')
            with open(meta_out) as handle:
                self.assertEqual(handle.read(), '{"last-known-good": true}')

    def test_snapshot_manifest_commits_matching_atomic_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = runner.config.RuntimeConfig(
                json_out=os.path.join(tmpdir, "events.json"),
                meta_json_out=os.path.join(tmpdir, "meta.json"),
            )
            metadata = {"run_id": "run-1", "generated_at": "2026-07-09T20:00:00", "run_status": "healthy"}
            paths = runner._publish_snapshots(settings, [{"title": "Event"}], metadata, "run-1")
            with open(paths["manifest"]) as handle:
                manifest = json.load(handle)

        self.assertEqual(manifest["run_id"], "run-1")
        self.assertEqual(manifest["event_count"], 1)

    def test_disabled_source_is_not_a_degraded_run(self):
        def disabled_fetch():
            runner.common.log_source_disabled("Optional Source", "disabled for test")
            return []

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {
                "NRW_EVENTS_JSON_OUT": os.path.join(tmpdir, "events.json"),
                "NRW_EVENTS_META_JSON_OUT": os.path.join(tmpdir, "meta.json"),
            }, clear=False), mock.patch.object(runner, "SOURCES", {"Optional Source": disabled_fetch}), \
                    mock.patch.object(runner.report, "format_report", lambda events: ""), \
                    mock.patch.object(sys, "argv", ["runner"]):
                self.assertEqual(runner.main(), runner.EXIT_SUCCESS)

            with open(os.path.join(tmpdir, "meta.json")) as handle:
                metadata = json.load(handle)
        self.assertEqual(metadata["source_results"]["Optional Source"]["status"], "disabled")

    def test_invalid_source_records_are_quarantined_with_reason_counts(self):
        def mixed_fetch():
            return [{
                "title": "Valid", "date": "2026-06-08", "time": "", "venue": "", "city": "Bonn",
                "description": "", "price": "", "link": "https://example.test", "distance_km": 0,
                "score": 1.0, "source": "Mixed", "category": "concert",
            }, {"title": "Invalid", "score": 1.0, "source": "Mixed"}]

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {
                "NRW_EVENTS_JSON_OUT": os.path.join(tmpdir, "events.json"),
                "NRW_EVENTS_META_JSON_OUT": os.path.join(tmpdir, "meta.json"),
            }, clear=False), mock.patch.object(runner, "SOURCES", {"Mixed": mixed_fetch}), \
                    mock.patch.object(runner.report, "format_report", lambda events: ""), \
                    mock.patch.object(sys, "argv", ["runner"]):
                self.assertEqual(runner.main(), runner.EXIT_DEGRADED)

            with open(os.path.join(tmpdir, "meta.json")) as handle:
                metadata = json.load(handle)
        result = metadata["source_results"]["Mixed"]
        self.assertEqual(result["accepted_event_count"], 1)
        self.assertEqual(result["rejection_reasons"], {"start_date_missing_or_invalid": 1})


if __name__ == "__main__":
    unittest.main()
