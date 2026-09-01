import io
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime
from unittest import mock

from nrw_events import common, core, report, runner
from nrw_events.identity import content_hash, event_id
from nrw_events.health import (
    MAX_REJECTION_SAMPLE_JSON_LENGTH,
    SourceFetchResult,
    SourceResult,
    SourceStatus,
)
from nrw_events import config
from nrw_events.observability import configure_logging, log
from nrw_events.runtime import EventWindow, RunContext
from nrw_events.sources import bonn_districts, regional_sitekit
from tests.helpers import default_window, make_runner_env, patch_window


class RunnerOutputTests(unittest.TestCase):
    def test_publication_source_resolution_prefers_exact_owner_over_aggregate_membership(self):
        event = runner.validate_event({
            "title": "Exact owner",
            "source": "Bonn.de Events",
            "source_id": "bonn-de-events",
            "date": "2026-09-02",
            "score": 0.2,
            "city": "Bonn",
        })
        aggregate = SourceResult(
            "Bonn district festivals",
            source_id="bonn-district-festivals",
            event_sources=["Bonn.de Events"],
            event_source_ids=["bonn-de-events"],
        )
        exact = SourceResult("Bonn.de Events", source_id="bonn-de-events")
        results = {
            "Bonn district festivals": aggregate,
            "Bonn.de Events": exact,
        }

        self.assertIs(runner._source_result_for_event(event, results), exact)

    def test_publication_ai_metrics_prefer_exact_owner_over_aggregate_membership(self):
        event = runner.validate_event({
            "title": "Exact AI owner",
            "source": "Bonn.de Events",
            "source_id": "bonn-de-events",
            "date": "2026-09-02",
            "score": 1.0,
            "city": "Bonn",
        })
        aggregate = SourceResult(
            "Bonn district festivals",
            source_id="bonn-district-festivals",
            event_sources=["Bonn.de Events"],
            event_source_ids=["bonn-de-events"],
        )
        exact = SourceResult("Bonn.de Events", source_id="bonn-de-events")
        results = {
            "Bonn district festivals": aggregate,
            "Bonn.de Events": exact,
        }

        runner._record_publication_ai_metrics(
            [event], results, {"bonn-de-events": {}}, 25, [event]
        )

        self.assertEqual(exact.ai_candidate_event_count, 1)
        self.assertEqual(exact.ai_enriched_event_count, 1)
        self.assertEqual(aggregate.ai_candidate_event_count, 0)

    def test_restricted_publication_boundary_removes_copy_adopted_during_dedup(self):
        canonical = runner.validate_event({
            "title": "Stadtgartenkonzert",
            "source": "Bonn.de Events",
            "source_id": "bonn-de-events",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
            "date": "2026-08-28",
            "time": "19:40",
            "venue": "Stadtgarten",
            "city": "Bonn",
            "score": 1.0,
            "description": "Quelltext, der nicht veröffentlicht werden darf.",
            "description_source": "scraped",
            "ai_summary": "Zulässige AI-Zusammenfassung.",
        })
        leaked_after_dedup = replace(
            canonical,
            description="Quelltext, der nicht veröffentlicht werden darf.",
            description_html="<p>Quelltext, der nicht veröffentlicht werden darf.</p>",
            description_source="scraped",
            ai_summary="",
        )

        [published] = runner._enforce_restricted_publication_boundary([leaked_after_dedup])

        self.assertEqual(published.description_source, "generated")
        self.assertNotIn("Quelltext", published.description)
        self.assertTrue(published.description)
        self.assertTrue(published.description_html)

    def test_restricted_publication_boundary_accepts_validated_mapping(self):
        event = {
            "title": "Mapping target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-08-28",
            "score": 1.0, "city": "Bonn",
            "description": "Restricted source prose.",
        }
        validated_mapping = runner.validate_event(event).to_dict()

        [published] = runner._enforce_restricted_publication_boundary([
            validated_mapping,
        ])

        self.assertNotIn("Restricted source prose", published.description)
        self.assertTrue(published.description)

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

    def test_ai_enriches_only_the_final_deduped_publishable_winner(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json="", score_floor=0.3),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "publication-ai", configure_logging("publication-ai", "ERROR", "", ""),
            clock=lambda: datetime(2026, 6, 8, 12),
        )
        winner = {
            "title": "Final target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "score": 1.0, "city": "Bonn", "venue": "",
            "link": "https://example.test/winner",
            "description": "Private winner source material.",
        }
        duplicate = {
            **winner,
            "score": 0.8,
            "link": "https://example.test/duplicate",
            "description": "Private duplicate source material.",
        }
        filtered = {
            **winner,
            "title": "Filtered target", "score": 0.1,
            "link": "https://example.test/filtered",
            "description": "Private filtered source material.",
        }
        outside = {
            **winner,
            "title": "Outside target", "date": "2026-07-01",
            "link": "https://example.test/outside",
            "description": "Private outside source material.",
        }
        seen_ids = []
        pre_ai_hashes = []

        def enrich_final(events, *, settings=None, stats=None, stats_by_source=None):
            self.assertEqual(["Final target"], [value["title"] for value in events])
            [value] = events
            self.assertEqual("Private winner source material.", value["description"])
            self.assertNotIn("Private duplicate", value["description"])
            seen_ids.append(event_id(value))
            pre_ai_hashes.append(value["content_hash"])
            self.assertTrue(pre_ai_hashes[-1])
            if stats is not None:
                stats.update({
                    "ai_deadline_skipped_event_count": 0,
                    "ai_cap_skipped_event_count": 0,
                    "ai_cache_budget_skipped_event_count": 0,
                    "ai_deadline_skipped_without_summary_event_count": 0,
                    "ai_cap_skipped_without_summary_event_count": 0,
                    "ai_cache_budget_skipped_without_summary_event_count": 0,
                })
            return [{
                **value,
                "description": "Provider attempted restricted copy.",
                "description_html": "<p>Provider attempted restricted copy.</p>",
                "description_source": "scraped",
                "ai_summary": "Reviewed generated summary.",
                "time": "19:30",
                "venue": "AI learned venue",
                "preserved_event_id": event_id(value),
            }]

        with mock.patch.object(runner.detail_enrichment, "enrich_events", side_effect=lambda events, **_: events), \
             mock.patch.object(runner.ai_enrichment, "enrich_events", side_effect=enrich_final) as enrich:
            result = runner.run_import(context, {
                "Bonn.de Events": lambda: [winner, duplicate, filtered, outside],
            })

        enrich.assert_called_once()
        [published] = result.events
        self.assertEqual("Reviewed generated summary.", published.ai_summary)
        self.assertNotIn("Provider attempted", published.description)
        self.assertNotIn("Provider attempted", published.description_html)
        self.assertEqual(seen_ids, [event_id(published)])
        self.assertEqual(content_hash(published.to_dict()), published.content_hash)
        self.assertNotEqual(pre_ai_hashes[0], published.content_hash)
        self.assertNotIn("Private", json.dumps(published.to_dict()))
        self.assertNotIn("Private", json.dumps(runner.build_snapshot(result, context).events))
        source_result = result.source_results["Bonn.de Events"]
        self.assertEqual(1, source_result.ai_candidate_event_count)
        self.assertEqual(1, source_result.ai_enriched_event_count)
        self.assertEqual(1, source_result.as_dict()["ai_enriched_event_count"])

    def test_publication_ai_keeps_winner_prose_after_dedup_fills_schedule_and_link(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "publication-ai-dedup-metadata",
            configure_logging("publication-ai-dedup-metadata", "ERROR", "", ""),
        )
        winner = {
            "title": "Metadata target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "score": 1.0, "city": "Bonn", "venue": "Marktplatz",
            "description": "Private winner programme prose.",
        }
        duplicate = {
            **winner,
            "score": 0.8,
            "time": "19:30",
            "link": "https://example.test/metadata-target",
            "description": "Private duplicate programme prose.",
        }

        def enrich_final(events, **_kwargs):
            [value] = events
            self.assertEqual("19:30", value["time"])
            self.assertEqual("https://example.test/metadata-target", value["link"])
            self.assertEqual("Private winner programme prose.", value["description"])
            return [{**value, "ai_summary": "Generated summary."}]

        with mock.patch.object(
            runner.detail_enrichment, "enrich_events", side_effect=lambda events, **_: events,
        ), mock.patch.object(
            runner.ai_enrichment, "enrich_events", side_effect=enrich_final,
        ):
            result = runner.run_import(
                context, {"Bonn.de Events": lambda: [winner, duplicate]},
            )

        [published] = result.events
        self.assertEqual("Generated summary.", published.ai_summary)

    def test_invalid_publication_ai_settings_fail_explicitly(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "invalid-publication-ai-settings",
            configure_logging("invalid-publication-ai-settings", "ERROR", "", ""),
        )
        raw = {
            "title": "Configuration target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "score": 1.0, "city": "Bonn",
        }

        with mock.patch.dict(os.environ, {"NRW_EVENTS_AI_WORKERS": "0"}), \
             mock.patch.object(
                 runner.detail_enrichment, "enrich_events", side_effect=lambda events, **_: events,
             ), mock.patch.object(runner.ai_enrichment, "enrich_events") as enrich:
            with self.assertRaisesRegex(
                ValueError, "NRW_EVENTS_AI_WORKERS must be between 1 and 16",
            ):
                runner.run_import(context, {"Bonn.de Events": lambda: [raw]})

        enrich.assert_not_called()

    def test_reviewed_summary_manifest_is_applied_before_billable_ai(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "reviewed-summary", configure_logging("reviewed-summary", "ERROR", "", ""),
            clock=lambda: datetime(2026, 6, 8, 12),
        )
        raw = {
            "title": "Reviewed target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "time": "19:30", "score": 1.0, "city": "Bonn",
            "venue": "Marktplatz", "link": "https://example.test/reviewed",
            "description": "Private reviewed source material.",
        }
        stable_id = event_id(runner.validate_event(raw))
        manifest = {
            "version": 1,
            "rules": [{
                "id": "reviewed-target",
                "match": {
                    "source_id": "bonn-de-events",
                    "title": "Reviewed target",
                    "event_ids": [stable_id],
                    "links": [raw["link"]],
                    "start_dates": ["2026-06-09"],
                    "times": ["19:30"],
                },
                "set": {"ai_summary": "Locally reviewed summary."},
                "evidence": {"verdict": "content_reviewed"},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reviewed.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.dict(os.environ, {"NRW_EVENTS_REVIEWED_AI_SUMMARIES_PATH": path}), \
                 mock.patch.object(runner.detail_enrichment, "enrich_events", side_effect=lambda events, **_: events), \
                 mock.patch.object(runner.ai_enrichment, "enrich_events") as enrich:
                result = runner.run_import(context, {"Bonn.de Events": lambda: [raw]})

        [published] = result.events
        self.assertEqual("Locally reviewed summary.", published.ai_summary)
        self.assertNotIn("Private reviewed", json.dumps(published.to_dict()))
        enrich.assert_not_called()
        self.assertEqual(
            0, result.source_results["Bonn.de Events"].ai_enriched_event_count,
        )

    def test_mismatched_reviewed_summary_guard_does_not_suppress_ai(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "stale-summary", configure_logging("stale-summary", "ERROR", "", ""),
            clock=lambda: datetime(2026, 6, 8, 12),
        )
        raw = {
            "title": "Current target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "time": "19:30", "score": 1.0, "city": "Bonn",
            "venue": "Marktplatz", "link": "https://example.test/current",
            "description": "Private current source material.",
        }
        manifest = {
            "version": 1,
            "rules": [{
                "id": "stale-title",
                "match": {
                    "source_id": "bonn-de-events", "title": "Old target",
                    "event_ids": [event_id(runner.validate_event(raw))],
                    "start_dates": ["2026-06-09"],
                },
                "set": {"ai_summary": "Stale reviewed summary."},
                "evidence": {"verdict": "content_reviewed"},
            }],
        }

        def generated(events, **_kwargs):
            return [{
                **events[0],
                "description": "", "description_html": "",
                "description_source": "generated",
                "ai_summary": "Fresh AI summary.",
            }]

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reviewed.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with mock.patch.dict(os.environ, {"NRW_EVENTS_REVIEWED_AI_SUMMARIES_PATH": path}), \
                 mock.patch.object(runner.detail_enrichment, "enrich_events", side_effect=lambda events, **_: events), \
                 mock.patch.object(runner.ai_enrichment, "enrich_events", side_effect=generated) as enrich:
                result = runner.run_import(context, {"Bonn.de Events": lambda: [raw]})

        self.assertEqual("Fresh AI summary.", result.events[0].ai_summary)
        enrich.assert_called_once()

    def test_malformed_reviewed_summary_manifest_fails_closed(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "bad-summary-manifest",
            configure_logging("bad-summary-manifest", "ERROR", "", ""),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reviewed.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"version": 2, "rules": []}, handle)
            with mock.patch.dict(os.environ, {"NRW_EVENTS_REVIEWED_AI_SUMMARIES_PATH": path}):
                with self.assertRaisesRegex(ValueError, "version must be 1"):
                    runner.run_import(context, {})

    def test_reviewed_summary_manifest_rejects_non_integer_version_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reviewed.json")
            for version in (True, 1.0):
                with self.subTest(version=version):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump({"version": version, "rules": []}, handle)
                    with self.assertRaisesRegex(ValueError, "version must be 1"):
                        runner.reviewed_summaries.apply_reviewed_summaries([], path)

    def test_malformed_reviewed_summary_rule_fails_closed(self):
        manifest = {
            "version": 1,
            "rules": [{
                "id": "missing-evidence",
                "match": {
                    "source_id": "bonn-de-events", "title": "Event",
                    "event_ids": ["event-id"], "start_dates": ["2026-06-09"],
                },
                "set": {"ai_summary": "Summary"},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reviewed.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with self.assertRaisesRegex(ValueError, "missing-evidence.*malformed"):
                runner.reviewed_summaries.apply_reviewed_summaries([], path)

    def test_reviewed_summary_manifest_rejects_unknown_keys_at_every_level(self):
        rule = {
            "id": "strict-rule",
            "match": {
                "source_id": "bonn-de-events", "title": "Event",
                "event_ids": ["event-id"], "start_dates": ["2026-06-09"],
            },
            "set": {"ai_summary": "Summary"},
            "evidence": {"verdict": "content_reviewed"},
        }
        malformed_manifests = {
            "root": {"version": 1, "rules": [rule], "unknown": True},
            "rule": {
                "version": 1, "rules": [{**rule, "unknown": True}],
            },
            "match": {
                "version": 1,
                "rules": [{**rule, "match": {**rule["match"], "unknown": True}}],
            },
            "evidence": {
                "version": 1,
                "rules": [{
                    **rule,
                    "evidence": {**rule["evidence"], "unknown": True},
                }],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reviewed.json")
            for level, manifest in malformed_manifests.items():
                with self.subTest(level=level):
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump(manifest, handle)
                    with self.assertRaisesRegex(ValueError, "unknown keys"):
                        runner.reviewed_summaries.apply_reviewed_summaries([], path)

    def test_identical_summary_rules_are_still_ambiguous(self):
        event = runner.validate_event({
            "title": "Ambiguous target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "score": 1.0, "city": "Bonn",
        })
        match = {
            "source_id": event.source_id,
            "title": event.title,
            "event_ids": [event_id(event)],
            "start_dates": [event.start_date],
        }
        manifest = {
            "version": 1,
            "rules": [
                {
                    "id": rule_id, "match": match,
                    "set": {"ai_summary": "Same reviewed summary."},
                    "evidence": {"verdict": "content_reviewed"},
                }
                for rule_id in ("first", "second")
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reviewed.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            [unchanged], warnings = runner.reviewed_summaries.apply_reviewed_summaries(
                [event], path,
            )

        self.assertEqual("", unchanged.ai_summary)
        self.assertEqual("ReviewedSummaryAmbiguityWarning", warnings[0]["error_type"])

    def test_reviewed_summary_preserves_non_restricted_description_and_provenance(self):
        event = runner.validate_event({
            "title": "Publisher-authored event", "source": "Official venue",
            "source_id": "official-venue", "date": "2026-06-09",
            "time": "19:30", "score": 1.0, "city": "Bonn",
            "link": "https://example.test/official",
            "description": "Canonical publisher-authored description.",
            "description_html": "<p>Canonical publisher-authored description.</p>",
            "description_source": "scraped",
        })
        manifest = {
            "version": 1,
            "rules": [{
                "id": "official-summary",
                "match": {
                    "source_id": event.source_id, "title": event.title,
                    "event_ids": [event_id(event)], "start_dates": [event.start_date],
                },
                "set": {"ai_summary": "Content-reviewed summary."},
                "evidence": {"verdict": "content_reviewed"},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reviewed.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            [reviewed], warnings = runner.reviewed_summaries.apply_reviewed_summaries(
                [event], path,
            )

        self.assertEqual([], warnings)
        self.assertEqual("Content-reviewed summary.", reviewed.ai_summary)
        self.assertEqual(event.description, reviewed.description)
        self.assertEqual(event.description_html, reviewed.description_html)
        self.assertEqual(event.description_source, reviewed.description_source)

    def test_empty_optional_reviewed_summary_guards_are_treated_as_omitted(self):
        event = runner.validate_event({
            "title": "Optional guards", "source": "Official venue",
            "source_id": "official-venue", "date": "2026-06-09",
            "time": "19:30", "score": 1.0, "city": "Bonn",
            "link": "https://example.test/optional",
        })
        for empty_guard in ("links", "times"):
            with self.subTest(empty_guard=empty_guard), tempfile.TemporaryDirectory() as directory:
                manifest = {
                    "version": 1,
                    "rules": [{
                        "id": f"empty-{empty_guard}",
                        "match": {
                            "source_id": event.source_id, "title": event.title,
                            "event_ids": [event_id(event)],
                            "start_dates": [event.start_date], empty_guard: [],
                        },
                        "set": {"ai_summary": f"Matched empty {empty_guard}."},
                        "evidence": {"verdict": "content_reviewed"},
                    }],
                }
                path = os.path.join(directory, "reviewed.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(manifest, handle)
                [reviewed], warnings = runner.reviewed_summaries.apply_reviewed_summaries(
                    [event], path,
                )

            self.assertEqual([], warnings)
            self.assertEqual(f"Matched empty {empty_guard}.", reviewed.ai_summary)

    def test_non_empty_optional_reviewed_summary_guards_remain_strict(self):
        event = runner.validate_event({
            "title": "Strict guards", "source": "Official venue",
            "source_id": "official-venue", "date": "2026-06-09",
            "time": "19:30", "score": 1.0, "city": "Bonn",
            "link": "https://example.test/current",
        })
        for guard, stale_value in (
            ("links", "https://example.test/stale"), ("times", "20:00"),
        ):
            with self.subTest(guard=guard), tempfile.TemporaryDirectory() as directory:
                manifest = {
                    "version": 1,
                    "rules": [{
                        "id": f"strict-{guard}",
                        "match": {
                            "source_id": event.source_id, "title": event.title,
                            "event_ids": [event_id(event)],
                            "start_dates": [event.start_date], guard: [stale_value],
                        },
                        "set": {"ai_summary": "Must not apply."},
                        "evidence": {"verdict": "content_reviewed"},
                    }],
                }
                path = os.path.join(directory, "reviewed.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(manifest, handle)
                [unchanged], warnings = runner.reviewed_summaries.apply_reviewed_summaries(
                    [event], path,
                )

            self.assertEqual([], warnings)
            self.assertEqual("", unchanged.ai_summary)

    def test_publication_ai_exception_keeps_sanitized_events_and_degrades_source(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "ai-exception", configure_logging("ai-exception", "ERROR", "", ""),
        )
        raw = {
            "title": "Fallback target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "score": 1.0, "city": "Bonn",
            "description": "Private fallback source material.",
        }
        with mock.patch.object(runner.detail_enrichment, "enrich_events", side_effect=lambda events, **_: events), \
             mock.patch.object(runner.ai_enrichment, "enrich_events", side_effect=RuntimeError("provider down")):
            result = runner.run_import(context, {"Bonn.de Events": lambda: [raw]})

        [published] = result.events
        self.assertNotIn("Private fallback", published.description)
        self.assertTrue(published.description)
        source_result = result.source_results["Bonn.de Events"]
        self.assertEqual(SourceStatus.DEGRADED, source_result.status)
        self.assertEqual(1, source_result.ai_skipped_event_count)
        self.assertEqual(1, source_result.ai_skipped_without_summary_event_count)
        self.assertTrue(any(
            warning["error_type"] == "AIEnrichmentBatchWarning"
            for warning in source_result.warnings
        ))

    def test_invalid_publication_ai_output_is_not_counted_as_enriched(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "invalid-ai-output",
            configure_logging("invalid-ai-output", "ERROR", "", ""),
        )
        raw = {
            "title": "Validation target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "score": 1.0, "city": "Bonn",
        }

        def invalid_output(events, **_kwargs):
            return [{**events[0], "title": "", "ai_summary": "Attempted summary."}]

        with mock.patch.object(
            runner.detail_enrichment, "enrich_events", side_effect=lambda events, **_: events,
        ), mock.patch.object(
            runner.ai_enrichment, "enrich_events", side_effect=invalid_output,
        ):
            result = runner.run_import(context, {"Bonn.de Events": lambda: [raw]})

        self.assertEqual("", result.events[0].ai_summary)
        source_result = result.source_results["Bonn.de Events"]
        self.assertEqual(1, source_result.ai_candidate_event_count)
        self.assertEqual(0, source_result.ai_enriched_event_count)
        self.assertEqual(0, source_result.as_dict()["ai_enriched_event_count"])
        self.assertTrue(any(
            warning["error_type"] == "AIEnrichmentValidationWarning"
            for warning in result.warnings
        ))

    def test_source_worker_defers_ai_and_telemetry_to_publication_stage(self):
        event = {
            "title": "Event", "source": "Bonn.de Events",
            "date": common.TODAY.strftime("%Y-%m-%d"), "score": 1.0, "city": "Bonn",
        }

        with mock.patch.object(runner.ai_enrichment, "enrich_events") as enrich:
            result, _ = runner._run_source("Bonn.de Events", lambda: [event])

        enrich.assert_not_called()
        self.assertEqual(result.ai_candidate_event_count, 0)
        self.assertEqual(result.ai_duration_ms, 0)

    def test_publication_ai_clears_private_source_material_when_there_are_no_candidates(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json="", score_floor=0.5),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "no-ai-candidates", configure_logging("no-ai-candidates", "ERROR", "", ""),
        )
        raw = {
            "title": "Filtered target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "score": 0.1, "city": "Bonn",
            "description": "Private filtered source material.",
        }

        with mock.patch.object(runner.ai_enrichment, "enrich_events") as enrich:
            result = runner.run_import(context, {"Bonn.de Events": lambda: [raw]})

        enrich.assert_not_called()
        self.assertEqual(
            [], result.source_results["Bonn.de Events"]._ai_source_material,
        )

    def test_publication_ai_clears_private_source_material_when_ai_is_disabled(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "ai-disabled", configure_logging("ai-disabled", "ERROR", "", ""),
        )
        raw = {
            "title": "Disabled target", "source": "Bonn.de Events",
            "source_id": "bonn-de-events", "date": "2026-06-09",
            "score": 1.0, "city": "Bonn",
            "description": "Private disabled source material.",
        }
        disabled_settings = mock.Mock(enabled=False)

        def disabled_enrichment(events, *, settings, **_kwargs):
            self.assertIs(disabled_settings, settings)
            return events

        with mock.patch.object(
            runner.ai_enrichment, "settings_from_env", return_value=disabled_settings,
        ), mock.patch.object(
            runner.ai_enrichment, "enrich_events", side_effect=disabled_enrichment,
        ):
            result = runner.run_import(context, {"Bonn.de Events": lambda: [raw]})

        self.assertEqual(
            [], result.source_results["Bonn.de Events"]._ai_source_material,
        )

    def test_runner_warns_only_for_skips_without_a_cached_summary(self):
        event = runner.validate_event({
            "title": "Event", "source": "Bonn.de Events",
            "source_id": "bonn-de-events",
            "date": common.TODAY.strftime("%Y-%m-%d"), "score": 1.0, "city": "Bonn",
        })
        result = SourceResult(
            "Bonn.de Events", source_id="bonn-de-events",
            event_source_ids=["bonn-de-events"],
        )

        runner._record_publication_ai_metrics(
            [event], {"Bonn.de Events": result},
            {"bonn-de-events": {
                "ai_deadline_skipped_event_count": 1,
                "ai_deadline_skipped_without_summary_event_count": 0,
            }},
            10,
        )

        self.assertEqual(1, result.ai_skipped_event_count)
        self.assertEqual(0, result.ai_skipped_without_summary_event_count)
        self.assertFalse(any(
            warning["error_type"] == "AIEnrichmentBudgetWarning"
            for warning in result.warnings
        ))

    def test_enriched_metric_aggregates_child_sources_per_source_result(self):
        events = [
            runner.validate_event({
                "title": title, "source": "Grouped source", "source_id": source_id,
                "date": common.TODAY.strftime("%Y-%m-%d"), "score": 1.0, "city": "Bonn",
            })
            for title, source_id in (
                ("Civic event", "bonn-de-events"),
                ("Sports event", "bonn-de-sports"),
            )
        ]
        result = SourceResult(
            "Grouped source", source_id="grouped-source",
            event_source_ids=["bonn-de-events", "bonn-de-sports"],
        )

        runner._record_publication_ai_metrics(
            events, {"Grouped source": result}, {}, 10, events,
        )

        self.assertEqual(2, result.ai_enriched_event_count)

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
        self.assertEqual(
            result.rejection_samples,
            {"record_not_object": {
                "source": "Malformed",
                "source_id": "malformed",
                "record_type": "NoneType",
            }},
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(validate.call_count, 1)

    def test_rejection_without_a_candidate_does_not_invent_a_none_sample(self):
        result = SourceResult("Filtered")

        result.reject("quality:expected-filter")

        self.assertEqual(result.rejection_reasons, {"quality:expected-filter": 1})
        self.assertEqual(result.rejection_samples, {})

    def test_rejection_samples_bound_hostile_fields_and_keep_runner_identity(self):
        hostile_title = "prefix\n\x00\x1b[31m" + ("💥" * 10_000)
        result, events = runner._run_source("Trusted Source", lambda: [{
            "title": hostile_title,
            "source": "Spoofed\nSource",
            "source_id": "spoofed-source",
            "date": common.TODAY.strftime("%Y-%m-%d"),
            "score": 1.0,
            "city": "Bonn",
            "link": "/invalid",
        }])

        self.assertEqual(events, [])
        sample = result.rejection_samples["title_too_long"]
        self.assertEqual(sample["source"], "Trusted Source")
        self.assertEqual(sample["source_id"], "trusted-source")
        self.assertNotIn("Spoofed", repr(sample))
        self.assertNotRegex(sample["title"], r"[\x00-\x1f\x7f]")
        self.assertLessEqual(len(sample["title"]), 200)
        self.assertLessEqual(
            len(json.dumps(sample, ensure_ascii=False).encode()),
            MAX_REJECTION_SAMPLE_JSON_LENGTH,
        )

        message = runner._source_issue_message(result, [])
        self.assertNotRegex(message, r"[\x00-\x1f\x7f]")
        self.assertLessEqual(len(message), 2048)

    def test_rejection_samples_replace_lone_surrogates_and_byte_cap_every_raw_field(self):
        result = SourceResult("Trusted Source")
        hostile = "💥" * 100 + "\ud800\n"

        result.reject("invalid", {
            "title": hostile,
            "date": hostile,
            "start_date": hostile,
            "end_date": hostile,
        })

        sample = result.rejection_samples["invalid"]
        serialized = json.dumps(sample, ensure_ascii=False).encode("utf-8")
        self.assertNotIn("\ud800", repr(sample))
        self.assertLessEqual(len(sample["title"].encode("utf-8")), 200)
        for field in ("date", "start_date", "end_date"):
            self.assertLessEqual(len(sample[field].encode("utf-8")), 32)
        self.assertLessEqual(len(serialized), MAX_REJECTION_SAMPLE_JSON_LENGTH)

    def test_lone_surrogate_in_rejected_record_does_not_fail_source(self):
        result, events = runner._run_source("Trusted Source", lambda: [{
            "title": "Invalid surrogate \ud800 title",
            "source": "Spoofed Source",
            "date": common.TODAY.strftime("%Y-%m-%d"),
            "score": 1.0,
            "city": "Bonn",
            "link": "/invalid",
        }])

        self.assertEqual(events, [])
        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertIsNone(result.error)
        json.dumps(result.as_dict(), ensure_ascii=False).encode("utf-8")

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
        self.assertEqual(result.rejection_samples, {
            "link_invalid": {
                "title": "Current event",
                "source": "Current",
                "source_id": "current",
                "date": "2026-07-29",
                "in_window": True,
            },
        })
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

        self.assertEqual(snapshot.metadata["snapshot_schema_version"], 7)
        self.assertEqual(event["date"], "2026-06-01")
        self.assertEqual(event["start_date"], "2026-06-01")
        self.assertEqual(event["end_date"], "2026-06-10")
        self.assertTrue(event["ongoing"])
        self.assertEqual(event["admission"]["isFree"], True)
        self.assertEqual(event["admission"]["basis"], "structured")


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
    def test_suppressed_retained_umbrella_is_not_counted_as_published(self):
        series_title = "Direct Festival Programme"
        with make_runner_env() as env:
            previous = {
                "generated_at": "2026-06-07T05:00:00",
                "source_results": {
                    "Bonn.de Events": {
                        "raw_event_count": 1,
                        "event_source_ids": ["bonn-de-events"],
                    },
                },
                "events": [{
                    "title": series_title,
                    "source": "Bonn.de Events",
                    "source_id": "bonn-de-events",
                    "date": "2026-06-09",
                    "venue": "Test venue",
                    "venue_id": "test-venue",
                    "score": 1.0,
                    "city": "Bonn",
                }],
            }
            env.previous_path.write_text(json.dumps(previous), encoding="utf-8")
            primary = {
                "title": "Concrete programme item",
                "source": "Direct Festival",
                "source_id": "direct-festival",
                "series_title": series_title,
                "date": "2026-06-09",
                "venue": "Test venue",
                "venue_id": "test-venue",
                "score": 1.0,
                "city": "Bonn",
            }

            result = runner.run_import(
                env.context("retained-umbrella", series_ledger_json=""),
                {
                    "Bonn.de Events": lambda: SourceFetchResult.parser_empty(),
                    "Direct Festival": lambda: [primary],
                },
            )

        self.assertEqual([event.title for event in result.events], ["Concrete programme item"])
        self.assertEqual(result.retention["retained_event_count"], 0)
        self.assertEqual(result.retention["fresh_event_count"], 1)
        self.assertEqual(result.retention["retained_sources"][0]["source_id"], "bonn-de-events")
        self.assertEqual(result.retention["retained_sources"][0]["retained_event_count"], 0)

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

    def test_cross_run_id_reconciliation_keeps_all_published_ids_linked_by_detail_urls(self):
        previous = {"events": [
            {"event_id": "canonical", "title": "Street Food Festival", "start_date": "2026-08-28", "source_id": "city-marketing", "time": "15:00", "venue": "Innenstadt", "city": "Bonn-Bad Godesberg", "link": "https://city.example/street-food", "link_kind": "detail", "source_links": ["https://city.example/street-food"]},
            {"event_id": "legacy", "title": "Street Food Festival", "start_date": "2026-08-28", "source_id": "bonn", "time": "15:00", "venue": "Innenstadt", "city": "Bonn-Bad Godesberg", "link": "https://bonn.example/street-food", "link_kind": "detail", "source_links": ["https://bonn.example/street-food"]},
        ]}
        current = [{
            "title": "Street Food Festival", "start_date": "2026-08-28",
            "source_id": "city-marketing", "time": "15:00", "venue": "Innenstadt",
            "city": "Bonn-Bad Godesberg", "link": "https://city.example/street-food",
            "link_kind": "detail", "source_links": [
                "https://city.example/street-food", "https://bonn.example/street-food",
            ],
        }]

        [reconciled] = runner._reconcile_published_ids(current, previous)

        self.assertEqual(reconciled["preserved_event_id"], "canonical")
        self.assertEqual(reconciled["previous_event_ids"], ["legacy"])

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

    def test_targeted_bonn_refresh_counts_promoted_primary_as_retained(self):
        with make_runner_env() as env:
            primary = {
                "title": "Atelier am Sonntag",
                "source": "Kunstmuseum Bonn",
                "source_id": "kunstmuseum-bonn",
                "date": "2026-06-09",
                "city": "Bonn",
                "venue": "Kunstmuseum Bonn",
                "description": "Primärtext des Kunstmuseums.",
                "description_source": "scraped",
                "link": "https://www.kunstmuseum-bonn.de/atelier/",
                "score": 1.0,
            }
            env.previous_path.write_text(json.dumps({
                "generated_at": "2026-06-07T08:00:00+00:00",
                "events": [primary],
                "source_results": {
                    "Kunstmuseum Bonn": {
                        "event_source_ids": ["kunstmuseum-bonn"],
                        "accepted_event_count": 1,
                    },
                },
            }), encoding="utf-8")
            municipal = {
                **primary,
                "source": "Bonn.de Events",
                "source_id": "bonn-de-events",
                "description": "Bonn-Kalendertext.",
                "description_source": "generated",
                "link": "https://www.bonn.de/atelier.php",
            }

            with mock.patch.object(
                runner.detail_enrichment,
                "enrich_events",
                side_effect=lambda events, **_kwargs: events,
            ):
                result = runner.run_import(env.context("targeted-bonn-retention"), {
                    "Kunstmuseum Bonn": lambda: SourceFetchResult.scheduled_skip(
                        "targeted refresh preserved kunstmuseum-bonn from the previous snapshot"
                    ),
                    "Bonn.de Events": lambda: [municipal],
                })

        [event] = result.events
        self.assertEqual(event.source_id, "kunstmuseum-bonn")
        self.assertEqual(result.retention["fresh_event_count"], 0)
        self.assertEqual(result.retention["retained_event_count"], 1)
        self.assertEqual(
            result.retention["retained_sources"][0]["retained_event_count"],
            1,
        )

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

    def test_prevalidation_title_warning_uses_adapter_identity_and_bounded_fields(self):
        title = "Hostile\n\x00\ud800" + ("💥" * 10_000) + " und"
        result, events = runner._run_source("Trusted Adapter", lambda: [{
            "title": title,
            "date": common.TODAY.strftime("%Y-%m-%d"),
            "venue": "Club",
            "city": "Bonn",
            "link": "https://example.test/event",
            "score": 1.0,
            "source": "Spoofed\nmarktcom",
            "source_id": "spoofed-id",
        }])

        self.assertEqual(events, [])
        [warning] = result.warnings
        self.assertEqual(warning["source"], "Trusted Adapter")
        self.assertEqual(warning["source_id"], "trusted-adapter")
        self.assertEqual(warning["error_type"], "TitleTruncationWarning")
        self.assertNotRegex(warning["error"], r"[\x00-\x1f\x7f-\x9f]")
        self.assertNotIn("\ud800", repr(warning))
        self.assertLessEqual(len(warning["error"].encode("utf-8")), 512)

    def test_raw_source_cannot_spoof_source_specific_truncation_detection(self):
        result, events = runner._run_source("Trusted Adapter", lambda: [{
            "title": "A sufficiently long source teaser ending...",
            "date": common.TODAY.strftime("%Y-%m-%d"),
            "venue": "Club",
            "city": "Bonn",
            "link": "https://example.test/event",
            "score": 1.0,
            "source": "marktcom",
            "source_id": "marktcom",
        }])

        self.assertEqual(len(events), 1)
        self.assertEqual(result.warnings, [])

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

    def test_snapshot_sanitizes_all_runner_warning_fields_at_persistence_boundary(self):
        context = RunContext(
            config.RuntimeConfig(series_ledger_json=""),
            EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10)),
            "warning-boundary", configure_logging("warning-boundary", "ERROR", "", ""),
            clock=lambda: datetime(2026, 6, 8, 12),
        )
        imported = runner.run_import(context, {})
        hostile = "raw\n\x00\ud800" + ("💥" * 1_000)
        imported = replace(imported, warnings=({
            "source": hostile,
            "source_id": hostile,
            "error_type": hostile,
            "error": hostile,
            "count": 7,
        },))

        snapshot = runner.build_snapshot(imported, context)
        [warning] = snapshot.metadata["source_warnings"]

        self.assertEqual(warning["count"], 7)
        self.assertLessEqual(len(warning["source"].encode("utf-8")), 100)
        self.assertLessEqual(len(warning["source_id"].encode("utf-8")), 100)
        self.assertLessEqual(len(warning["error_type"].encode("utf-8")), 100)
        self.assertLessEqual(len(warning["error"].encode("utf-8")), 512)
        self.assertNotRegex(repr(warning), r"[\x00-\x1f\x7f-\x9f]|\\ud800")
        json.dumps(snapshot.metadata, ensure_ascii=False).encode("utf-8")

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
