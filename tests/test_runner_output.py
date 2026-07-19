import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from scripts.nrw_events import common, report, runner
from scripts.nrw_events.health import SourceFetchResult, SourceStatus
from scripts.nrw_events import config
from scripts.nrw_events.observability import configure_logging
from scripts.nrw_events.runtime import EventWindow, RunContext
from scripts.nrw_events.sources import bonn_districts, regional_sitekit


class RunnerOutputTests(unittest.TestCase):
    def test_snapshot_builder_is_pure_with_fixed_context(self):
        canonical = runner.validate_event({
            "title": "Event", "source": "Memory", "date": "2026-06-08",
            "score": 1.0, "city": "Bonn",
        })
        result = runner.ImportResult((canonical,), {}, 1, "healthy")
        context = RunContext(config.RuntimeConfig(), EventWindow(
            datetime(2026, 6, 8), datetime(2026, 6, 10)), "fixed",
            configure_logging("fixed", "ERROR", "", ""),
            clock=lambda: datetime(2026, 6, 8, 12),
        )
        self.assertEqual(runner.build_snapshot(result, context),
                         runner.build_snapshot(result, context))

    def test_typed_source_result_distinguishes_adapter_states(self):
        self.assertEqual(SourceFetchResult.success([]).status, SourceStatus.HEALTHY_EMPTY)
        self.assertEqual(SourceFetchResult.disabled("missing key").status, SourceStatus.DISABLED)
        self.assertEqual(SourceFetchResult.parser_empty().status, SourceStatus.PARSER_EMPTY)

    def test_runner_preserves_typed_partial_success(self):
        result, events = runner._run_source("Typed", lambda: SourceFetchResult.partial([
            {"title": "Event", "source": "Typed", "date": common.TODAY.strftime("%Y-%m-%d"),
             "score": 1.0, "city": "Bonn"},
        ], "one endpoint failed"))
        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertEqual(len(events), 1)
        self.assertEqual(result.event_sources, ["Typed"])

    def test_unavailable_grouped_subsource_retains_only_unexpired_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            previous = {
                "generated_at": "2026-06-07T05:00:00",
                "source_results": {
                    "Regional HTML calendars": {
                        "raw_event_count": 2,
                        "event_sources": ["Lohmar"],
                    },
                },
                "events": [
                    {
                        "title": "Expired Lohmar Event", "source": "Lohmar",
                        "date": "2026-06-07", "score": 1.0, "city": "Lohmar",
                    },
                    {
                        "title": "Upcoming Lohmar Event", "source": "Lohmar",
                        "date": "2026-06-09", "score": 1.0, "city": "Lohmar",
                    },
                ],
            }
            with open(previous_path, "w") as handle:
                json.dump(previous, handle)

            def partial_group():
                runner.common.log_source_error("Lohmar", TimeoutError("read timed out"))
                return [{
                    "title": "Fresh Bornheim Event", "source": "Bornheim",
                    "date": "2026-06-09", "score": 1.0, "city": "Bornheim",
                }]

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
                "retention-test", configure_logging("retention-test", "ERROR", "", ""),
                clock=lambda: datetime(2026, 6, 8, 5),
            )
            result = runner.run_import(context, {"Regional HTML calendars": partial_group})
            snapshot = runner.build_snapshot(result, context).metadata

        self.assertEqual({event.title for event in result.events}, {
            "Fresh Bornheim Event", "Upcoming Lohmar Event",
        })
        self.assertEqual(snapshot["fresh_event_count"], 1)
        self.assertEqual(snapshot["retained_event_count"], 1)
        self.assertEqual(snapshot["expired_retained_event_count"], 1)
        self.assertEqual(snapshot["retained_sources"], [{
            "source": "Lohmar",
            "source_id": "lohmar",
            "runner_source": "Regional HTML calendars",
            "retained_event_count": 1,
            "expired_event_count": 1,
            "last_success_at": "2026-06-07T05:00:00",
            "consecutive_failures": 1,
        }])

    def test_healthy_source_replaces_previous_snapshot_instead_of_retaining_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-06-07T05:00:00",
                    "source_results": {
                        "Lohmar": {"raw_event_count": 1, "event_sources": ["Lohmar"]},
                    },
                    "events": [{
                        "title": "Old Event", "source": "Lohmar",
                        "date": "2026-06-09", "score": 1.0, "city": "Lohmar",
                    }],
                }, handle)

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
                "recovery-test", configure_logging("recovery-test", "ERROR", "", ""),
            )
            result = runner.run_import(context, {"Lohmar": lambda: [{
                "title": "Fresh Event", "source": "Lohmar",
                "date": "2026-06-10", "score": 1.0, "city": "Lohmar",
            }]})

        self.assertEqual([event.title for event in result.events], ["Fresh Event"])
        self.assertEqual(result.retention["retained_event_count"], 0)
        self.assertEqual(result.retention["retained_sources"], [])

    def test_shared_display_source_retains_only_failed_logical_child(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            cached_bad_honnef = {
                "title": "Cached Bad Honnef", "source": "ionas4 regional",
                "date": "2026-06-09", "score": 1.0, "city": "Bad Honnef",
            }
            cached_grafschaft = {
                "title": "Cached Grafschaft", "source": "ionas4 regional",
                "date": "2026-06-09", "score": 1.0, "city": "Grafschaft",
            }
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-06-07T05:00:00",
                    "events": [cached_bad_honnef, cached_grafschaft],
                    "source_results": {},
                }, handle)

            def partial_ionas():
                common.log_source_error(
                    "ionas4 regional (Bad Honnef)", TimeoutError("timed out"),
                    source_id="ionas4-bad-honnef",
                )
                return [{
                    "title": "Fresh Grafschaft", "source": "ionas4 regional",
                    "source_id": "ionas4-grafschaft", "date": "2026-06-09",
                    "score": 1.0, "city": "Grafschaft",
                }]

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
                "child-id-test", configure_logging("child-id-test", "ERROR", "", ""),
            )
            result = runner.run_import(context, {"ionas4 regional": partial_ionas})
            snapshot = runner.build_snapshot(result, context).metadata

        titles = {event.title for event in result.events}
        self.assertIn("Fresh Grafschaft", titles)
        self.assertIn("Cached Bad Honnef", titles)
        self.assertNotIn("Cached Grafschaft", titles)
        self.assertEqual(snapshot["retained_sources"][0]["source_id"], "ionas4-bad-honnef")
        self.assertEqual(
            snapshot["retained_sources"][0]["source"],
            "ionas4 regional (Bad Honnef)",
        )

    def test_sitekit_migration_retains_only_failed_city(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-06-07T05:00:00",
                    "events": [
                        {"title": "Cached Brühl", "source": "SiteKit regional",
                         "date": "2026-06-09", "score": 1.0, "city": "Brühl"},
                        {"title": "Cached Wesseling", "source": "SiteKit regional",
                         "date": "2026-06-09", "score": 1.0, "city": "Wesseling"},
                    ],
                    "source_results": {},
                }, handle)

            def partial_sitekit():
                common.log_source_error(
                    "SiteKit regional (Brühl)", TimeoutError("timed out"),
                    source_id="sitekit-bruehl",
                )
                return [{
                    "title": "Fresh Wesseling", "source": "SiteKit regional",
                    "source_id": "sitekit-wesseling", "date": "2026-06-09",
                    "score": 1.0, "city": "Wesseling",
                }]

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
                "sitekit-child-test", configure_logging("sitekit-child-test", "ERROR", "", ""),
            )
            result = runner.run_import(context, {"SiteKit regional": partial_sitekit})

        self.assertEqual({event.title for event in result.events}, {
            "Cached Brühl", "Fresh Wesseling",
        })
        self.assertEqual(result.retention["retained_sources"][0]["source_id"], "sitekit-bruehl")
        self.assertEqual(result.retention["retained_sources"][0]["runner_source"], "SiteKit regional")

    def test_sitekit_parser_assigns_stable_child_source_id(self):
        html = """
        <article class="SP-Teaser">
          <a class="SP-Teaser__inner" href="/calendar/concert">
            <span class="SP-Scheduling__date">09.06.2026</span>
            <h4 class="SP-Teaser__headline">Brühler Konzert</h4>
            <div class="SP-Teaser__abstract">Musik im Rathaus.</div>
          </a>
        </article>
        """
        events = regional_sitekit._events_from_teasers(
            html, "https://www.bruehl.de/calendar", "Brühl", 0.9, "sitekit-bruehl"
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source"], "SiteKit regional")
        self.assertEqual(events[0]["source_id"], "sitekit-bruehl")

    def test_authoritative_empty_rest_collection_clears_hardtberg(self):
        with mock.patch("scripts.nrw_events.common.fetch_url", return_value="[]"):
            result, events = runner._run_source(
                "Hardtberg Kultur", bonn_districts.fetch_hardtberg
            )

        self.assertEqual(events, [])
        self.assertEqual(result.status, SourceStatus.HEALTHY_EMPTY)

    def test_zero_event_retention_survives_consecutive_grouped_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-06-07T05:00:00",
                    "events": [],
                    "source_results": {"SiteKit regional": {"event_source_ids": []}},
                    "retained_sources": [{
                        "source": "SiteKit regional (Brühl)",
                        "source_id": "sitekit-bruehl",
                        "runner_source": "SiteKit regional",
                        "retained_event_count": 0,
                        "expired_event_count": 1,
                        "last_success_at": "2026-06-06T05:00:00",
                        "consecutive_failures": 1,
                    }],
                }, handle)

            def still_partial():
                common.log_source_error(
                    "SiteKit regional (Brühl)", TimeoutError("still timed out"),
                    source_id="sitekit-bruehl",
                )
                return [{
                    "title": "Fresh Wesseling", "source": "SiteKit regional",
                    "source_id": "sitekit-wesseling", "date": "2026-06-09",
                    "score": 1.0, "city": "Wesseling",
                }]

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
                "sitekit-consecutive-test",
                configure_logging("sitekit-consecutive-test", "ERROR", "", ""),
            )
            result = runner.run_import(context, {"SiteKit regional": still_partial})

        retained = result.retention["retained_sources"]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["source_id"], "sitekit-bruehl")
        self.assertEqual(retained[0]["retained_event_count"], 0)
        self.assertEqual(retained[0]["consecutive_failures"], 2)

    def test_retained_event_after_current_window_is_not_published(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-06-07T05:00:00",
                    "events": [{
                        "title": "Too Far Ahead", "source": "Lohmar",
                        "source_id": "lohmar", "date": "2026-06-20",
                        "score": 1.0, "city": "Lohmar",
                    }],
                    "source_results": {"Lohmar": {"event_source_ids": ["lohmar"]}},
                }, handle)

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
                "upper-window-test", configure_logging("upper-window-test", "ERROR", "", ""),
            )
            result = runner.run_import(
                context,
                {"Lohmar": lambda: SourceFetchResult.parser_empty("layout changed")},
            )

        self.assertEqual(result.events, ())
        self.assertEqual(result.retention["retained_event_count"], 0)
        self.assertEqual(result.retention["retained_sources"][0]["source_id"], "lohmar")

    def test_partial_source_keeps_missing_prior_events_while_fresh_wins(self):
        def event(title: str, date: str, venue: str = "") -> dict:
            return {
                "title": title,
                "source": "Lohmar",
                "source_id": "lohmar",
                "date": date,
                "score": 1.0,
                "city": "Lohmar",
                "venue": venue,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_path = os.path.join(temp_dir, "previous.json")
            previous_events = [
                event("Keep fresh", "2026-06-09", "Old venue"),
                event("Temporarily missing", "2026-06-10"),
            ]
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-06-07T08:00:00+00:00",
                    "events": previous_events,
                    "source_results": {
                        "Lohmar": {
                            "event_source_ids": ["lohmar"],
                        },
                    },
                }, handle)

            def partial_source():
                common.log_source_error(
                    "Lohmar",
                    RuntimeError("one endpoint timed out"),
                    source_id="lohmar",
                )
                return [event("Keep fresh", "2026-06-09", "Fresh venue")]

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
                "partial-test",
                configure_logging("partial-test", "ERROR", "", ""),
            )
            result = runner.run_import(context, {"Lohmar": partial_source})

        self.assertEqual([event.title for event in result.events], ["Keep fresh", "Temporarily missing"])
        self.assertEqual(result.events[0].venue, "Fresh venue")
        self.assertEqual(result.retention["retained_event_count"], 1)

    def test_fresh_duplicate_wins_wholesale_over_retained_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_path = os.path.join(temp_dir, "previous.json")
            with open(previous_path, "w") as handle:
                json.dump({
                    "snapshot_schema_version": 1,
                    "generated_at": "2026-06-07T05:00:00+02:00",
                    "events": [{
                        "title": "Shared Event",
                        "source": "Official Calendar",
                        "source_id": "official-calendar",
                        "date": "2026-06-09",
                        "description": "Old retained description that must not enrich the fresh record.",
                        "score": 99.0,
                        "city": "Bonn",
                    }],
                    "source_results": {
                        "Broken": {
                            "event_source_ids": ["official-calendar"],
                            "accepted_event_count": 1,
                        },
                    },
                }, handle)

            def broken_source():
                common.log_source_error(
                    "Official Calendar", RuntimeError("temporary timeout"),
                    source_id="official-calendar",
                )
                return []

            def fresh_source():
                return [{
                    "title": "Shared Event",
                    "source": "Meetup",
                    "source_id": "meetup-fresh",
                    "date": "2026-06-09",
                    "description": "Fresh description.",
                    "score": 1.0,
                    "city": "Bonn",
                }]

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
                "fresh-wins-test", configure_logging("fresh-wins-test", "ERROR", "", ""),
            )
            result = runner.run_import(
                context,
                {"Broken": broken_source, "Fresh": fresh_source},
            )

        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].source, "Meetup")
        self.assertEqual(result.events[0].description, "Fresh description.")
        self.assertEqual(result.retention["fresh_event_count"], 1)
        self.assertEqual(result.retention["retained_event_count"], 0)

    def test_expected_quality_rejections_do_not_degrade_source_health(self):
        result, events = runner._run_source("Filtered", lambda: [
            {"title": "Concert", "source": "Filtered", "date": common.TODAY.strftime("%Y-%m-%d"),
             "score": 1.0, "city": "Bonn", "category": "konzert"},
            {"title": "Deutschkurs für Männer", "source": "Filtered",
             "date": common.TODAY.strftime("%Y-%m-%d"), "score": 1.0,
             "city": "Bonn", "category": "kurs"},
        ])

        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.rejected_event_count, 1)
        self.assertEqual(
            result.rejection_reasons,
            {"quality:legacy.editorial-policy": 1},
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(runner._import_issues({"Filtered": result}), [])

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
            with open(manifest["events_path"]) as handle:
                immutable_events = json.load(handle)
            with open(manifest["metadata_path"]) as handle:
                immutable_metadata = json.load(handle)

        self.assertEqual(manifest["run_id"], "run-1")
        self.assertEqual(manifest["event_count"], 1)
        self.assertEqual(immutable_events, [{"title": "Event"}])
        self.assertEqual(immutable_metadata["run_id"], "run-1")
        self.assertNotEqual(manifest["events_path"], paths["events"])
        self.assertNotEqual(manifest["metadata_path"], paths["metadata"])

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
