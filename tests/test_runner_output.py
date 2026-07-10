import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from scripts.nrw_events import common, report, runner


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
                "source": "Healthy Source",
                "category": "konzert",
            }]

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
                with mock.patch.object(runner, "SOURCES", {
                    "Fragile Source": fetch_with_warning,
                    "Healthy Source": fetch_event,
                }):
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
        self.assertEqual(meta_payload["import_issues"][0]["source"], "Fragile Source")
        self.assertIn("layout changed", meta_payload["import_issues"][0]["message"])

    def test_single_failed_source_does_not_fail_the_import_when_events_are_available(self):
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
                "source": "Healthy Source",
                "category": "konzert",
            }]

        def broken_fetch():
            raise RuntimeError("temporary source outage")

        with tempfile.TemporaryDirectory() as tmpdir:
            json_out = os.path.join(tmpdir, "events.json")
            meta_out = os.path.join(tmpdir, "events-meta.json")
            with mock.patch.dict(os.environ, {
                "NRW_EVENTS_JSON_OUT": json_out,
                "NRW_EVENTS_META_JSON_OUT": meta_out,
            }, clear=False), mock.patch.object(runner, "SOURCES", {
                "Broken Source": broken_fetch,
                "Healthy Source": fetch_event,
            }), mock.patch.object(runner.report, "format_report", lambda events: ""), \
                    mock.patch.object(sys, "argv", ["runner"]):
                self.assertEqual(runner.main(), runner.EXIT_SUCCESS)

            with open(json_out) as f:
                events_payload = json.load(f)
            with open(meta_out) as f:
                meta_payload = json.load(f)

        self.assertEqual(events_payload[0]["title"], "Concert")
        self.assertEqual(meta_payload["run_status"], "degraded")
        self.assertEqual(meta_payload["source_errors"], {"Broken Source": "temporary source outage"})
        self.assertEqual(meta_payload["import_issues"][0]["source"], "Broken Source")
        self.assertEqual(meta_payload["import_issues"][0]["severity"], "error")
        self.assertIn("temporary source outage", meta_payload["import_issues"][0]["message"])

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
                "source": "Healthy Source",
                "category": "konzert",
            }]

        def disabled_fetch():
            runner.common.log_source_disabled("Optional Source", "disabled for test")
            return []

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {
                "NRW_EVENTS_JSON_OUT": os.path.join(tmpdir, "events.json"),
                "NRW_EVENTS_META_JSON_OUT": os.path.join(tmpdir, "meta.json"),
            }, clear=False), mock.patch.object(runner, "SOURCES", {
                "Healthy Source": fetch_event,
                "Optional Source": disabled_fetch,
            }), \
                    mock.patch.object(runner.report, "format_report", lambda events: ""), \
                    mock.patch.object(sys, "argv", ["runner"]):
                self.assertEqual(runner.main(), runner.EXIT_SUCCESS)

            with open(os.path.join(tmpdir, "meta.json")) as handle:
                metadata = json.load(handle)
        self.assertEqual(metadata["source_results"]["Optional Source"]["status"], "disabled")

    def test_invalid_source_records_are_quarantined_with_reason_counts(self):
        def mixed_fetch():
            return [{
                "title": "Valid", "date": common.TODAY.strftime("%Y-%m-%d"), "time": "", "venue": "", "city": "Bonn",
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

    def test_recent_nonempty_source_drop_is_recorded_as_baseline_anomaly(self):
        result = runner.SourceResult(source="Source", raw_event_count=0)
        runner._attach_baselines({"Source": result}, {"Source": {"raw_event_count": 12}}, 10)
        self.assertEqual(result.anomalies, ["zero_after_recent_nonempty"])

    def test_baseline_anomaly_is_included_in_import_issues(self):
        result = runner.SourceResult(source="Source", raw_event_count=0)
        runner._attach_baselines({"Source": result}, {"Source": {"raw_event_count": 12}}, 10)

        issues = runner._import_issues({"Source": result})

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["source"], "Source")
        self.assertEqual(issues[0]["severity"], "warning")
        self.assertEqual(issues[0]["anomalies"], ["zero_after_recent_nonempty"])

    def test_repair_descriptions_do_not_trigger_nightlife_bucket(self):
        event = common.make_event(
            "Repair Café MVA Bonn - Fahrrad, Geräte, Nähen",
            common.TODAY.replace(hour=18, minute=30),
            common.TODAY.replace(hour=20, minute=30),
            "Repair Café MVA Bonn",
            "Bonn",
            "SMD Löttechnik sowie Akku-Technologien sind ein wichtiges Thema.",
            "https://www.repaircafesbonn.de/mc-events/test/",
            "Repair Cafés Bonn",
            "repair café reparatur offene werkstatt",
        )

        self.assertIsNotNone(event)
        rendered = report.format_report([event])
        self.assertIn("Talks, Community & Culture (1)", rendered)
        self.assertNotIn("Nightlife & Electronic (1)", rendered)


if __name__ == "__main__":
    unittest.main()
