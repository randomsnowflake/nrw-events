import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common, core, report, runner
from nrw_events.health import SourceFetchResult, SourceStatus
from nrw_events import config
from nrw_events.observability import configure_logging, log
from nrw_events.runtime import EventWindow, RunContext
from nrw_events.sources import bonn_districts, regional_sitekit
from tests.helpers import default_window, make_runner_env, patch_window


class RunnerOutputTests(unittest.TestCase):
    def test_logger_emits_identical_worker_warning_once_but_keeps_info_progress(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            logger = configure_logging("dedupe", "INFO", "", "")
        stream = io.StringIO()
        logger.handlers[0].setStream(stream)

        for _ in range(2):
            log(logger, 30, "request budget exhausted", run_id="dedupe", source="Detail")
            log(logger, 20, "progress", run_id="dedupe", source="Detail")

        output = stream.getvalue()
        self.assertEqual(output.count("request budget exhausted"), 1)
        self.assertEqual(output.count("progress"), 2)

    def test_repeated_source_warning_is_recorded_and_logged_once(self):
        def noisy_source():
            common.log_source_error("Noisy detail", TimeoutError("request budget exhausted"))
            common.log_source_error("Noisy detail", TimeoutError("request budget exhausted"))
            return []

        with mock.patch.object(core, "log") as emit:
            result, _ = runner._run_source("Noisy", noisy_source)

        self.assertEqual(len(result.warnings), 1)
        emit.assert_called_once()

    def test_expected_quality_rejections_are_summarized_without_per_record_logs(self):
        def filtered_source():
            common.log_source_quality_skip("Filtered", "civic.course")
            common.log_source_quality_skip("Filtered", "civic.course")
            return []

        with mock.patch.object(core, "log") as emit:
            result, _ = runner._run_source("Filtered", filtered_source)

        self.assertEqual(result.rejection_reasons, {"quality:civic.course": 2})
        emit.assert_not_called()

    def test_run_import_flushes_shared_detail_caches_once_after_all_sources(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "cache-flush", configure_logging("cache-flush", "ERROR", "", ""),
            clock=lambda: datetime(2026, 6, 8, 12),
        )
        event = {
            "title": "Event", "source": "Source A", "date": "2026-06-08",
            "score": 1.0, "city": "Bonn",
        }

        with mock.patch.object(common, "flush_detail_page_caches") as flush:
            runner.run_import(context, {
                "Source A": lambda: [event],
                "Source B": lambda: [{**event, "title": "Other Event", "source": "Source B"}],
            })

        flush.assert_called_once_with()

    def test_cache_flush_failure_is_exported_as_runner_warning(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "cache-warning", configure_logging("cache-warning", "ERROR", "", ""),
        )
        warning = {
            "source": "detail-cache", "error_type": "OSError",
            "error": "failed to persist cache",
        }
        with mock.patch.object(common, "flush_detail_page_caches", return_value=[warning]):
            result = runner.run_import(context, {})

        self.assertIn(warning, result.warnings)

    def test_runner_records_ai_worker_duration_and_candidate_count(self):
        event = {
            "title": "Event", "source": "Bonn.de Events",
            "date": common.TODAY.strftime("%Y-%m-%d"), "score": 1.0, "city": "Bonn",
        }

        def delayed_enrichment(events):
            time.sleep(0.01)
            return events

        with mock.patch.object(runner.ai_enrichment, "is_target_event", return_value=True), \
             mock.patch.object(runner.ai_enrichment, "enrich_events", side_effect=delayed_enrichment):
            result, _ = runner._run_source("Bonn.de Events", lambda: [event])

        self.assertEqual(result.ai_candidate_event_count, 1)
        self.assertGreaterEqual(result.ai_duration_ms, 5)
        self.assertEqual(result.as_dict()["ai_duration_ms"], result.ai_duration_ms)

    def test_discovery_candidates_are_sanitized_research_leads_before_enrichment(self):
        event = {
            "title": "Discovered event", "source": "Radio Bonn/Rhein-Sieg",
            "source_id": "radio-bonn-rhein-sieg", "source_role": "discovery",
            "discovered_via": ["radio-bonn-rhein-sieg"],
            "date": common.TODAY.strftime("%Y-%m-%d"), "score": 1.0,
            "city": "Bonn", "venue": "Marktplatz",
            "link": "https://www.radiobonn.de/artikel/tipps", "link_kind": "overview",
            "description": "Publisher prose must stay private.",
            "description_html": "<p>Publisher prose must stay private.</p>",
            "ai_summary": "Generated copy must stay private.",
        }

        with mock.patch.object(runner.detail_enrichment, "enrich_events") as detail, \
             mock.patch.object(runner.ai_enrichment, "enrich_events") as ai:
            result, events = runner._run_source("Radio Bonn/Rhein-Sieg", lambda: [event])

        self.assertEqual(events, [])
        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.raw_event_count, 1)
        self.assertEqual(result.accepted_event_count, 0)
        self.assertEqual(result.research_lead_reasons, {"needs_primary_source": 1})
        self.assertEqual(result.research_lead_count, 1)
        [lead] = result.research_leads
        self.assertEqual(lead["title"], "Discovered event")
        self.assertEqual(lead["source_role"], "discovery")
        self.assertEqual(lead["discovered_via"], ["radio-bonn-rhein-sieg"])
        self.assertEqual(lead["reason"], "needs_primary_source")
        self.assertFalse({"description", "description_html", "ai_summary"} & lead.keys())
        detail.assert_not_called()
        ai.assert_not_called()

    def test_import_timings_are_exported_in_snapshot_metadata(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "timings", configure_logging("timings", "ERROR", "", ""),
            clock=lambda: datetime(2026, 6, 8, 12),
        )
        event = {
            "title": "Event", "source": "Source A", "date": "2026-06-08",
            "score": 1.0, "city": "Bonn",
        }

        result = runner.run_import(context, {"Source A": lambda: [event]})
        snapshot = runner.build_snapshot(result, context)

        self.assertEqual(snapshot.metadata["timings"], result.timings)
        self.assertEqual(set(result.timings), {
            "source_import_duration_ms",
            "ai_processing_duration_ms",
            "total_import_duration_ms",
        })
        self.assertGreaterEqual(
            result.timings["total_import_duration_ms"],
            result.timings["source_import_duration_ms"],
        )

    def test_runner_filters_window_after_source_health_is_recorded(self):
        with mock.patch.object(common, "TODAY", datetime(2026, 7, 19)), \
             mock.patch.object(common, "END_DATE", datetime(2026, 8, 1)):
            outside = common.make_event(
                "September market", datetime(2026, 9, 1), None,
                "Market square", "Bonn", "Seasonal market",
                "https://example.test/september", "Seasonal", "market",
            )
            result, events = runner._run_source("Seasonal", lambda: [outside])

        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.raw_event_count, 1)
        self.assertEqual(result.accepted_event_count, 0)
        self.assertEqual(events, [])

    def test_runner_rejects_non_object_records_before_window_filtering(self):
        with mock.patch.object(
            runner, "validate_event", wraps=runner.validate_event
        ) as validate:
            result, events = runner._run_source("Malformed", lambda: [
                None,
                {"title": "Event", "source": "Malformed", "date": common.TODAY.strftime("%Y-%m-%d"),
                 "score": 1.0, "city": "Bonn"},
            ])

        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertEqual(result.rejection_reasons, {"record_not_object": 1})
        self.assertEqual(len(events), 1)
        self.assertEqual(validate.call_count, 1)

    def test_runner_rejects_malformed_date_types_without_dropping_valid_siblings(self):
        current_date = common.TODAY.strftime("%Y-%m-%d")
        result, events = runner._run_source("Malformed", lambda: [
            {"title": "Bad start date", "source": "Malformed", "start_date": 123,
             "score": 1.0, "city": "Bonn"},
            {"title": "Bad legacy date", "source": "Malformed", "date": 123,
             "score": 1.0, "city": "Bonn"},
            {"title": "Valid event", "source": "Malformed", "date": current_date,
             "score": 1.0, "city": "Bonn"},
        ])

        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertIsNone(result.error)
        self.assertEqual(result.rejection_reasons, {"start_date_type": 1, "date_type": 1})
        self.assertEqual([event.title for event in events], ["Valid event"])

    def test_runner_ignores_structural_defects_outside_the_report_window(self):
        with mock.patch.object(common, "TODAY", datetime(2026, 7, 19)), \
             mock.patch.object(common, "END_DATE", datetime(2026, 8, 1)):
            result, events = runner._run_source("Archive", lambda: [{
                "title": "Old event", "source": "Archive", "date": "2025-11-02",
                "score": 1.0, "city": "Bonn", "link": "mailto:old@example.test",
            }])

        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.rejection_reasons, {})
        self.assertEqual(events, [])

    def test_runner_still_rejects_structural_defects_inside_the_report_window(self):
        with mock.patch.object(common, "TODAY", datetime(2026, 7, 19)), \
             mock.patch.object(common, "END_DATE", datetime(2026, 8, 1)):
            result, events = runner._run_source("Current", lambda: [{
                "title": "Current event", "source": "Current", "date": "2026-07-29",
                "score": 1.0, "city": "Bonn", "link": "/relative/event",
            }])

        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertEqual(result.rejection_reasons, {"link_invalid": 1})
        self.assertEqual(events, [])

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

    def test_schema_v4_snapshot_has_strict_dates_and_structured_admission(self):
        canonical = runner.validate_event({
            "title": "Ongoing exhibition",
            "source": "Museum",
            "date": "ongoing until 2026-06-10",
            "start_date": "2026-06-01",
            "end_date": "2026-06-10",
            "description": "Der Eintritt ist frei.",
            "score": 1.0,
            "city": "Bonn",
        })
        result = runner.ImportResult((canonical,), {}, 1, "healthy")
        context = RunContext(
            config.RuntimeConfig(),
            default_window(),
            "fixed",
            configure_logging("fixed", "ERROR", "", ""),
            clock=lambda: datetime(2026, 6, 8, 12),
        )

        snapshot = runner.build_snapshot(result, context)
        event = snapshot.events[0]

        self.assertEqual(snapshot.metadata["snapshot_schema_version"], 4)
        self.assertEqual(event["date"], "2026-06-01")
        self.assertEqual(event["start_date"], "2026-06-01")
        self.assertEqual(event["end_date"], "2026-06-10")
        self.assertTrue(event["ongoing"])
        self.assertEqual(event["admission"]["isFree"], True)
        self.assertEqual(event["admission"]["basis"], "inferred")


class SourceHealthTests(unittest.TestCase):
    def test_typed_source_result_distinguishes_adapter_states(self):
        self.assertEqual(SourceFetchResult.success([]).status, SourceStatus.HEALTHY_EMPTY)
        self.assertEqual(SourceFetchResult.disabled("missing key").status, SourceStatus.DISABLED)
        self.assertEqual(
            SourceFetchResult.scheduled_skip("weekly").status,
            SourceStatus.SCHEDULED_SKIP,
        )
        self.assertEqual(SourceFetchResult.parser_empty().status, SourceStatus.PARSER_EMPTY)

    def test_unmeasured_legacy_empty_result_is_parser_empty(self):
        result, events = runner._run_source("Legacy direct fetch", lambda: [])

        self.assertEqual(events, [])
        self.assertEqual(result.status, SourceStatus.PARSER_EMPTY)

    def test_typed_authoritative_empty_result_stays_healthy_empty(self):
        result, events = runner._run_source(
            "Typed empty", lambda: SourceFetchResult.success([]),
        )

        self.assertEqual(events, [])
        self.assertEqual(result.status, SourceStatus.HEALTHY_EMPTY)

    def test_typed_empty_with_transport_warning_is_degraded(self):
        def timed_out_source():
            common.log_source_error("Timed out endpoint", TimeoutError("request timed out"))
            return SourceFetchResult.success([])

        result, events = runner._run_source("Timed out", timed_out_source)

        self.assertEqual(events, [])
        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertEqual(len(result.warnings), 1)

    def test_typed_empty_does_not_override_explicit_parser_failure(self):
        def parser_drift():
            common._record_endpoint("https://example.test/feed", parser_empty=True)
            return SourceFetchResult.success([])

        result, events = runner._run_source("Typed parser drift", parser_drift)

        self.assertEqual(events, [])
        self.assertEqual(result.status, SourceStatus.PARSER_EMPTY)

    def test_runner_preserves_typed_partial_success(self):
        result, events = runner._run_source("Typed", lambda: SourceFetchResult.partial([
            {"title": "Event", "source": "Typed", "date": common.TODAY.strftime("%Y-%m-%d"),
             "score": 1.0, "city": "Bonn"},
        ], "one endpoint failed"))
        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertEqual(len(events), 1)
        self.assertEqual(result.event_sources, ["Typed"])


class CrossRunRetentionTests(unittest.TestCase):
    def test_failed_discovery_source_does_not_retain_legacy_public_snapshot(self):
        with make_runner_env() as env:
            previous = {
                "generated_at": "2026-06-07T05:00:00",
                "source_results": {
                    "Radio Bonn/Rhein-Sieg": {
                        "raw_event_count": 1,
                        "event_source_ids": ["radio-bonn-rhein-sieg"],
                    },
                },
                "events": [{
                    "title": "Legacy Radio event", "source": "Radio Bonn/Rhein-Sieg",
                    "source_id": "radio-bonn-rhein-sieg", "date": "2026-06-09",
                    "score": 1.0, "city": "Bonn",
                }],
            }
            env.previous_path.write_text(json.dumps(previous), encoding="utf-8")

            result = runner.run_import(
                env.context("failed-discovery", series_ledger_json=""),
                {"Radio Bonn/Rhein-Sieg": lambda: SourceFetchResult.parser_empty()},
            )

        self.assertEqual(result.source_results["Radio Bonn/Rhein-Sieg"].status,
                         SourceStatus.PARSER_EMPTY)
        self.assertEqual(result.events, ())
        self.assertEqual(result.retention["retained_event_count"], 0)

    def test_legacy_discovery_cancellation_is_not_republished(self):
        with make_runner_env() as env:
            previous = {
                "generated_at": "2026-06-07T05:00:00",
                "events": [{
                    "title": "Cancelled legacy Radio event",
                    "source": "Radio Bonn/Rhein-Sieg",
                    "source_id": "radio-bonn-rhein-sieg",
                    "date": "2026-06-09", "status": "cancelled",
                    "score": 1.0, "city": "Bonn",
                }],
            }

            env.previous_path.write_text(json.dumps(previous), encoding="utf-8")
            result = runner.run_import(
                env.context("legacy-discovery-cancellation", series_ledger_json=""),
                {"Official Calendar": lambda: SourceFetchResult.success([])},
            )

        self.assertEqual(result.events, ())

    def test_healthy_discovery_refresh_does_not_resurrect_old_public_snapshot(self):
        with make_runner_env() as env:
            previous = {
                "generated_at": "2026-06-07T05:00:00",
                "source_results": {
                    "Radio Bonn/Rhein-Sieg": {
                        "raw_event_count": 1,
                        "event_source_ids": ["radio-bonn-rhein-sieg"],
                    },
                },
                "events": [{
                    "title": "Old Radio event", "source": "Radio Bonn/Rhein-Sieg",
                    "source_id": "radio-bonn-rhein-sieg", "date": "2026-06-09",
                    "score": 1.0, "city": "Bonn",
                }],
            }
            env.previous_path.write_text(json.dumps(previous), encoding="utf-8")
            discovery = {
                "title": "New Radio lead", "source": "Radio Bonn/Rhein-Sieg",
                "source_id": "radio-bonn-rhein-sieg", "source_role": "discovery",
                "discovered_via": ["radio-bonn-rhein-sieg"],
                "date": "2026-06-09", "score": 1.0, "city": "Bonn",
                "description": "Do not publish this copy.",
                "description_html": "<p>Do not publish this copy.</p>",
            }
            primary = {
                "title": "Primary event", "source": "Official Calendar",
                "date": "2026-06-09", "score": 1.0, "city": "Bonn",
            }

            result = runner.run_import(env.context("discovery-refresh", series_ledger_json=""), {
                "Radio Bonn/Rhein-Sieg": lambda: [discovery],
                "Official Calendar": lambda: [primary],
            })
            snapshot = runner.build_snapshot(result, env.context(
                "discovery-refresh", series_ledger_json="",
            ))

        self.assertEqual([event.title for event in result.events], ["Primary event"])
        radio_result = result.source_results["Radio Bonn/Rhein-Sieg"]
        self.assertEqual(radio_result.status, SourceStatus.HEALTHY)
        self.assertEqual(radio_result.raw_event_count, 1)
        self.assertEqual(snapshot.metadata["retained_event_count"], 0)
        self.assertEqual(snapshot.metadata["research_lead_count"], 1)
        self.assertEqual(snapshot.metadata["research_lead_reasons"], {"needs_primary_source": 1})
        self.assertNotIn("research_leads", snapshot.metadata)
        serialized = json.loads(json.dumps(snapshot.metadata))
        self.assertEqual(
            serialized["source_results"]["Radio Bonn/Rhein-Sieg"]["research_lead_count"],
            1,
        )
        self.assertNotIn(
            "research_leads",
            serialized["source_results"]["Radio Bonn/Rhein-Sieg"],
        )

    def test_unavailable_grouped_subsource_retains_only_unexpired_events(self):
        with make_runner_env() as env:
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
            env.previous_path.write_text(json.dumps(previous), encoding="utf-8")

            def partial_group():
                runner.common.log_source_error("Lohmar", TimeoutError("read timed out"))
                return [{
                    "title": "Fresh Bornheim Event", "source": "Bornheim",
                    "date": "2026-06-09", "score": 1.0, "city": "Bornheim",
                }]

            context = env.context(
                "retention-test",
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

    def test_scheduled_skip_retains_unexpired_events_without_degrading_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-07-20T05:00:00",
                    "source_results": {
                        "vomFASS Bonn": {
                            "raw_event_count": 12,
                            "event_source_ids": ["vomfass-bonn"],
                        },
                    },
                    "events": [
                        {
                            "title": "Expired Tasting", "source": "vomFASS Bonn",
                            "source_id": "vomfass-bonn", "date": "2026-07-23",
                            "score": 1.0, "city": "Bonn",
                        },
                        {
                            "title": "Upcoming Tasting", "source": "vomFASS Bonn",
                            "source_id": "vomfass-bonn", "date": "2026-07-27",
                            "score": 1.0, "city": "Bonn",
                        },
                    ],
                    "retained_sources": [{
                        "source": "vomFASS Bonn",
                        "source_id": "vomfass-bonn",
                        "runner_source": "vomFASS Bonn",
                        "retained_event_count": 2,
                        "expired_event_count": 0,
                        "last_success_at": "2026-07-20T05:00:00",
                        "consecutive_failures": 0,
                    }],
                }, handle)

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 7, 24), datetime(2026, 8, 20)),
                "scheduled-skip-test",
                configure_logging("scheduled-skip-test", "ERROR", "", ""),
                clock=lambda: datetime(2026, 7, 24, 5),
            )
            result = runner.run_import(
                context,
                {"vomFASS Bonn": lambda: SourceFetchResult.scheduled_skip("Mondays only")},
            )
            metadata = runner.build_snapshot(result, context).metadata

        self.assertEqual([event.title for event in result.events], ["Upcoming Tasting"])
        self.assertEqual(result.run_status, "healthy")
        self.assertEqual(metadata["import_issues"], [])
        self.assertEqual(
            metadata["source_results"]["vomFASS Bonn"]["status"],
            "scheduled_skip",
        )
        self.assertEqual(metadata["retained_event_count"], 1)
        self.assertEqual(metadata["expired_retained_event_count"], 1)
        self.assertEqual(metadata["retained_sources"][0]["consecutive_failures"], 0)

    def test_retained_enriched_event_keeps_its_published_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-07-20T05:00:00",
                    "source_results": {
                        "Calendar": {
                            "raw_event_count": 1,
                            "event_source_ids": ["calendar"],
                        },
                    },
                    "events": [{
                        "title": "Sommerkonzert", "source": "Calendar",
                        "source_id": "calendar", "date": "2026-07-27",
                        "time": "20:00", "start_at": "2026-07-27T20:00:00+02:00",
                        "score": 1.0, "city": "Bonn",
                        "event_id": "sommerkonzert-2026-07-27-original",
                    }],
                }, handle)

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path),
                EventWindow(datetime(2026, 7, 24), datetime(2026, 8, 20)),
                "retained-id-test",
                configure_logging("retained-id-test", "ERROR", "", ""),
                clock=lambda: datetime(2026, 7, 24, 5),
            )
            result = runner.run_import(
                context,
                {"Calendar": lambda: SourceFetchResult.parser_empty("temporarily empty")},
            )
            snapshot = runner.build_snapshot(result, context)

        self.assertEqual(
            snapshot.events[0]["event_id"],
            "sommerkonzert-2026-07-27-original",
        )
        self.assertNotIn("preserved_event_id", snapshot.events[0])

    def test_healthy_refresh_keeps_published_id_when_identity_metadata_grows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous_path = os.path.join(tmpdir, "previous.json")
            with open(previous_path, "w") as handle:
                json.dump({
                    "generated_at": "2026-07-20T05:00:00",
                    "source_results": {
                        "Calendar": {
                            "raw_event_count": 1,
                            "event_source_ids": ["calendar"],
                        },
                    },
                    "events": [{
                        "title": "Sommerkonzert", "source": "Calendar",
                        "source_id": "calendar", "date": "2026-07-27",
                        "start_date": "2026-07-27", "end_date": "2026-07-27",
                        "time": "", "venue": "", "score": 1.0, "city": "Bonn",
                        "event_id": "sommerkonzert-2026-07-27-original",
                    }],
                }, handle)

            context = RunContext(
                config.RuntimeConfig(previous_meta_json=previous_path, series_ledger_json=""),
                EventWindow(datetime(2026, 7, 24), datetime(2026, 8, 20)),
                "healthy-id-test",
                configure_logging("healthy-id-test", "ERROR", "", ""),
                clock=lambda: datetime(2026, 7, 24, 5),
            )
            result = runner.run_import(context, {"Calendar": lambda: [{
                "title": "Sommerkonzert", "source": "Calendar",
                "source_id": "calendar", "date": "2026-07-27",
                "start_date": "2026-07-27", "end_date": "2026-07-27",
                "time": "20:00", "venue": "Stadtgarten", "score": 1.0,
                "city": "Bonn",
            }]})
            snapshot = runner.build_snapshot(result, context)

        self.assertEqual(
            snapshot.events[0]["event_id"],
            "sommerkonzert-2026-07-27-original",
        )

    def test_cross_run_id_reconciliation_accepts_dict_tombstones(self):
        tombstone = {
            "title": "Abgesagtes Sommerfest", "source": "Calendar",
            "source_id": "calendar", "date": "2026-07-27",
            "start_date": "2026-07-27", "end_date": "2026-07-27",
            "time": "20:00", "venue": "Stadtgarten", "city": "Bonn",
            "status": "cancelled",
        }
        previous = {"events": [{**tombstone, "event_id": "published-tombstone-id"}]}

        [reconciled] = runner._reconcile_published_ids([tombstone], previous)

        self.assertEqual(reconciled["preserved_event_id"], "published-tombstone-id")

    def test_cross_run_id_reconciliation_keeps_same_day_sessions_distinct(self):
        previous = {"events": [
            {"event_id": "early", "title": "Workshop", "start_date": "2026-07-27", "source_id": "calendar", "time": "14:00", "venue": "Studio", "city": "Bonn", "link": "https://example.test/calendar"},
            {"event_id": "late", "title": "Workshop", "start_date": "2026-07-27", "source_id": "calendar", "time": "17:00", "venue": "Studio", "city": "Bonn", "link": "https://example.test/calendar"},
        ]}
        current = [
            {"title": "Workshop", "start_date": "2026-07-27", "source_id": "calendar", "time": "17:00", "venue": "Studio", "city": "Bonn", "link": "https://example.test/calendar"},
            {"title": "Workshop", "start_date": "2026-07-27", "source_id": "calendar", "time": "14:00", "venue": "Studio", "city": "Bonn", "link": "https://example.test/calendar"},
        ]

        reconciled = runner._reconcile_published_ids(current, previous)

        self.assertEqual([event["preserved_event_id"] for event in reconciled], ["late", "early"])

    def test_cross_run_id_reconciliation_skips_ambiguous_groups(self):
        previous = {"events": [
            {"event_id": "one", "title": "Open Day", "start_date": "2026-07-27", "source_id": "calendar", "city": "Bonn", "link": "https://example.test/calendar"},
            {"event_id": "two", "title": "Open Day", "start_date": "2026-07-27", "source_id": "calendar", "city": "Bonn", "link": "https://example.test/calendar"},
        ]}
        current = [
            {"title": "Open Day", "start_date": "2026-07-27", "source_id": "calendar", "city": "Bonn", "link": "https://example.test/calendar"},
            {"title": "Open Day", "start_date": "2026-07-27", "source_id": "calendar", "city": "Bonn", "link": "https://example.test/calendar"},
        ]

        reconciled = runner._reconcile_published_ids(current, previous)

        self.assertTrue(all("preserved_event_id" not in event for event in reconciled))

    def test_cross_run_id_reconciliation_preserves_unique_upstream_title_expansion(self):
        previous = {"events": [{
            "event_id": "published", "title": "Klassizismus und Gotik",
            "start_date": "2026-08-15", "source_id": "bonn-de-events",
            "venue": "Alter Friedhof in Bonn", "city": "Bonn",
        }]}
        current = [{
            "title": "Alter Friedhof - Themenführung: Klassizismus und Gotik",
            "start_date": "2026-08-15", "source_id": "bonn-de-events",
            "venue": "Alter Friedhof", "city": "Bonn",
        }]

        [reconciled] = runner._reconcile_published_ids(current, previous)

        self.assertEqual(reconciled["preserved_event_id"], "published")

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
                default_window(),
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
                default_window(),
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
                default_window(),
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
        with mock.patch("nrw_events.common.fetch_url", return_value="[]"):
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
                default_window(),
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
                default_window(),
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
                default_window(),
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
                default_window(),
                "fresh-wins-test", configure_logging("fresh-wins-test", "ERROR", "", ""),
            )
            result = runner.run_import(
                context,
                {"Broken": broken_source, "Fresh": fresh_source},
            )

        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].source, "Meetup")
        self.assertNotIn("Fresh description", result.events[0].description)
        self.assertIn("Shared Event", result.events[0].description)
        self.assertEqual(result.events[0].description_source, "generated")
        self.assertEqual(result.retention["fresh_event_count"], 1)
        self.assertEqual(result.retention["retained_event_count"], 0)


class EventQualityTests(unittest.TestCase):
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
            {"quality:civic.course": 1},
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(runner._import_issues({"Filtered": result}), [])

    def test_unavailable_events_are_filtered_at_the_canonical_boundary(self):
        result, events = runner._run_source("Availability", lambda: [
            {
                "title": "Belcanto", "source": "Availability",
                "date": common.TODAY.strftime("%Y-%m-%d"), "score": 1.0,
                "city": "Bonn", "description": "Konzert – Ausverkauft",
            },
            {
                "title": "Nachtwache", "source": "Availability",
                "date": common.TODAY.strftime("%Y-%m-%d"), "score": 1.0,
                "city": "Bonn",
                "description": "Wenn die Türen geschlossen sind, beginnt das Escape Game.",
            },
        ])

        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.rejected_event_count, 1)
        self.assertEqual(
            result.rejection_reasons,
            {"quality:availability.unavailable": 1},
        )
        self.assertEqual([event.title for event in events], ["Nachtwache"])
        self.assertEqual(runner._import_issues({"Availability": result}), [])

    def test_make_event_quality_drops_are_counted_by_named_rule(self):
        def fetch_events():
            kept = common.make_event(
                "Sommerkonzert", common.TODAY, common.TODAY, "Club", "Bonn",
                "Live-Musik", "https://example.test/concert", "Measured Source",
                "konzert",
            )
            dropped = common.make_event(
                "Deutschkurs für Männer", common.TODAY, common.TODAY,
                "Bürgerzentrum", "Bonn", "Sprachkurs",
                "https://example.test/course", "Measured Source", "kurs",
            )
            return [event for event in (kept, dropped) if event]

        result, events = runner._run_source("Measured Source", fetch_events)

        self.assertEqual(len(events), 1)
        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.rejected_event_count, 1)
        self.assertEqual(result.rejection_reasons, {"quality:civic.course": 1})

    def test_truncated_marketcom_title_is_published_with_a_validation_warning(self):
        title = "Film-, Comic- & Figurenbörse in der STADTHALLE KÖ..."

        def fetch_events():
            return [{
                "title": title,
                "date": common.TODAY.strftime("%Y-%m-%d"),
                "venue": "Stadthalle",
                "city": "Köln",
                "description": "Comic- und Manga-Convention",
                "link": "https://example.test/market",
                "distance_km": 25,
                "score": 1.0,
                "source": "marktcom",
                "category": "markt",
            }]

        result, events = runner._run_source("marktcom", fetch_events)

        self.assertEqual(len(events), 1)
        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.warnings, [{
            "source": "marktcom",
            "source_id": "marktcom",
            "error_type": "TitleTruncationWarning",
            "error": f"title may be truncated: {title}",
        }])

    def setUp(self):
        patch_window(self, datetime(2026, 6, 8), datetime(2026, 6, 10))


class SnapshotPublicationTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 6, 8), datetime(2026, 6, 10))

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
                    with mock.patch.object(runner.report, "format_report", lambda events, **kwargs: ""):
                        with mock.patch.object(sys, "argv", ["runner"]):
                            runner.main()

            with open(json_out) as f:
                events_payload = json.load(f)
            with open(meta_out) as f:
                meta_payload = json.load(f)

        self.assertIsInstance(events_payload, list)
        self.assertEqual(events_payload[0]["title"], "Concert")
        self.assertEqual(events_payload[0]["category_key"], "concert")
        self.assertEqual(events_payload[0]["category_label"], "Konzert")
        self.assertGreater(events_payload[0]["category_confidence"], 0)
        self.assertIn("concert", events_payload[0]["category_reason"])
        self.assertIsInstance(meta_payload, dict)
        self.assertNotIn("events", meta_payload)
        self.assertEqual(meta_payload["events_path"], json_out)
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
                    with mock.patch.object(runner.report, "format_report", lambda events, **kwargs: ""):
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
            }), mock.patch.object(runner.report, "format_report", lambda events, **kwargs: ""), \
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
                    mock.patch.object(runner.report, "format_report", lambda events, **kwargs: ""), \
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
                highlights_json_out=os.path.join(tmpdir, "site", "highlights.json"),
                series_ledger_json=os.path.join(tmpdir, "state", "series.json"),
            )
            metadata = {"run_id": "run-1", "generated_at": "2026-07-09T20:00:00", "run_status": "healthy"}
            highlights = {"schemaVersion": "1.0", "run_id": "run-1", "categories": []}
            ledger = {"schema_version": 1, "series": {}}
            with mock.patch.object(
                runner.fcntl, "flock", wraps=runner.fcntl.flock
            ) as flock:
                paths = runner._publish_snapshots(
                    settings, [{"title": "Event"}], metadata, "run-1",
                    highlights=highlights, series_ledger=ledger,
                )
            with open(paths["manifest"]) as handle:
                manifest = json.load(handle)
            with open(manifest["events_path"]) as handle:
                immutable_events = json.load(handle)
            with open(manifest["metadata_path"]) as handle:
                immutable_metadata = json.load(handle)
            with open(manifest["highlights_path"]) as handle:
                immutable_highlights = json.load(handle)
            with open(paths["series_ledger"]) as handle:
                published_ledger = json.load(handle)
            flock.assert_called_once()
            self.assertEqual(flock.call_args.args[1], runner.fcntl.LOCK_EX)
            self.assertTrue(os.path.isfile(paths["manifest"] + ".lock"))

        self.assertEqual(manifest["run_id"], "run-1")
        self.assertEqual(manifest["event_count"], 1)
        self.assertEqual(immutable_events, [{"title": "Event"}])
        self.assertEqual(immutable_metadata["run_id"], "run-1")
        self.assertEqual(immutable_highlights["run_id"], "run-1")
        self.assertEqual(published_ledger, ledger)
        self.assertNotEqual(manifest["events_path"], paths["events"])
        self.assertNotEqual(manifest["metadata_path"], paths["metadata"])
        self.assertNotEqual(manifest["highlights_path"], paths["highlights"])

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
                    mock.patch.object(runner.report, "format_report", lambda events, **kwargs: ""), \
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
                    mock.patch.object(runner.report, "format_report", lambda events, **kwargs: ""), \
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
        self.assertEqual(event["category_key"], "workshop")
        rendered = report.format_report([event])
        self.assertIn("Talks, Community & Culture (1)", rendered)
        self.assertNotIn("Nightlife & Electronic (1)", rendered)

    def test_report_escapes_remote_markdown_and_wraps_links(self):
        event = common.make_event(
            "# Stern_*[Titel]`", common.TODAY, common.TODAY,
            "Saal_[1]", "Bonn", "Text_mit *Markup* und `Code`.",
            "https://example.test/a_(b)", "Source_[remote]", "Konzert",
        )

        rendered = report.format_report([event])

        self.assertIn(r"\# Stern\_\*\[Titel\]\`", rendered)
        self.assertIn(r"Saal\_\[1\]", rendered)
        self.assertIn(r"Text\_mit \*Markup\* und \`Code\`", rendered)
        self.assertIn("<https://example.test/a_(b)>", rendered)
        self.assertIn(r"Source\_\[remote\]", rendered)

    def test_report_obeys_character_and_section_limits(self):
        events = []
        for index in range(12):
            event = common.make_event(
                f"Konzert {index}", common.TODAY, common.TODAY,
                "Saal", "Bonn", "Lange Beschreibung " * 20,
                f"https://example.test/{index}", "Source", "Konzert",
            )
            events.append(event)

        section_limited = report.format_report(events, max_per_section=2)
        char_limited = report.format_report(events, max_chars=500)

        self.assertIn("… und 10 weitere", section_limited)
        self.assertLessEqual(len(char_limited), 500)
        self.assertRegex(char_limited, r"… und \d+ weitere Events \(Ausgabe gekürzt\)")

        uncapped_events = [
            {**events[0], "title": f"Ungekürztes Konzert {index}"}
            for index in range(60)
        ]
        uncapped = report.format_report(uncapped_events)
        self.assertGreater(len(uncapped), 16_000)
        self.assertIn("Ungekürztes Konzert 59", uncapped)

    def test_report_ends_with_actionable_metadata_gap_hints(self):
        events = [
            {
                "title": "Unklarer Termin", "source": "Sparse Source",
                "category_key": "other", "location_confidence": "unresolved",
                "city": "Beispielort", "venue": "", "date": "2026-08-03",
                "score": 1.0, "distance_km": None, "description": "", "link": "",
            },
            {
                "title": "Zweiter Termin", "source": "Sparse Source",
                "category_key": "concert", "location_confidence": "known_city",
                "city": "Bonn", "venue": "", "date": "2026-08-04",
                "score": 1.0, "distance_km": 0, "description": "", "link": "",
            },
        ]

        rendered = report.format_report(events)

        self.assertIn("### Ergänzungshinweise", rendered)
        self.assertIn("Kategorie ergänzen: Termine auf Sonstiges: 1", rendered)
        self.assertIn("Ortschaft prüfen: geografisch nicht aufgelöste Termine: 1", rendered)
        self.assertIn("Veranstaltungsort ergänzen: Termine ohne Venue: 2", rendered)


if __name__ == "__main__":
    unittest.main()
