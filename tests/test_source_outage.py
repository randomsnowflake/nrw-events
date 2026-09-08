"""Offline end-to-end source outage policy through run_import/build_snapshot."""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from nrw_events import runner
from nrw_events.health import SourceFetchResult
from nrw_events.runtime import EventWindow
from tests.helpers import make_event, make_runner_env

START = datetime(2026, 6, 8, 5, tzinfo=timezone.utc)


def event(source="Calendar", title="Concert", **kwargs):
    return make_event(source=source, title=title, date="2026-06-25",
                      start_date="2026-06-25", end_date="2026-06-25", **kwargs)


def failed():
    raise TimeoutError("upstream unavailable")


class SourceOutageTests(unittest.TestCase):
    def run_day(self, env, day, sources):
        now = START + timedelta(days=day)
        context = replace(env.context(clock=lambda: now, series_ledger_json=""),
                          window=EventWindow.from_days(28, now))
        result = runner.run_import(context, sources)
        snapshot = runner.build_snapshot(result, context)
        payload = {**snapshot.metadata, "events": snapshot.events}
        env.previous_path.write_text(json.dumps(payload))
        return result, payload

    def sources(self, calendar):
        return {"Calendar": calendar, "Healthy": lambda: [event("Healthy", "Play")]}

    def test_elapsed_days_same_day_recovery_and_reoutage(self):
        with make_runner_env() as env:
            self.run_day(env, -1, self.sources(lambda: [event()]))
            first = None
            for day in (0, 0, 0.5, 4, 5, 6, 7, 8):
                with self.subTest(day=day):
                    result, payload = self.run_day(env, day, self.sources(failed))
                    retained = payload["retained_sources"][0]
                    first = first or retained["first_failure_at"]
                    self.assertEqual(retained["first_failure_at"], first)
                    self.assertEqual(result.run_status, "degraded")
                    self.assertEqual("Concert" in {e.title for e in result.events}, day < 7)
            self.run_day(env, 9, self.sources(lambda: [event()]))
            _, payload = self.run_day(env, 10, self.sources(failed))
            self.assertNotEqual(payload["retained_sources"][0]["first_failure_at"], first)
            self.assertEqual(payload["retained_event_count"], 1)

    def test_no_previous_events_parser_empty_is_tracked(self):
        with make_runner_env() as env:
            for day in (0, 5, 7):
                result, payload = self.run_day(env, day, self.sources(SourceFetchResult.parser_empty))
                self.assertEqual(result.run_status, "degraded")
                self.assertEqual(payload["retained_sources"][0]["source_id"], "calendar")
                self.assertEqual(payload["retained_sources"][0]["first_failure_at"], START.isoformat(timespec="seconds"))
                self.assertEqual(payload["retained_event_count"], 0)

    def test_healthy_empty_resets_even_with_raw_drop_anomaly(self):
        with make_runner_env() as env:
            _, old = self.run_day(env, -1, self.sources(lambda: [event()]))
            old["source_results"]["Calendar"]["raw_event_count"] = 100
            env.previous_path.write_text(json.dumps(old))
            _, payload = self.run_day(env, 0, self.sources(lambda: SourceFetchResult.success([])))
            self.assertEqual(payload["retained_sources"], [])
            self.assertEqual(payload["retained_event_count"], 0)

    def test_partial_source_keeps_fresh_rows_and_expires_only_cached_rows(self):
        with make_runner_env() as env:
            self.run_day(env, -1, self.sources(lambda: [event()]))
            partial = lambda: SourceFetchResult.partial([event(title="Fresh concert")], "endpoint failed")
            for day in (0, 6, 7):
                result, _ = self.run_day(env, day, self.sources(partial))
                titles = {e.title for e in result.events}
                self.assertIn("Fresh concert", titles)
                self.assertEqual("Concert" in titles, day < 7)

    def test_empty_partial_without_diagnostics_preserves_outage_clock(self):
        for seed in (False, True):
            with self.subTest(seed=seed), make_runner_env() as env:
                if seed:
                    self.run_day(env, -1, self.sources(lambda: [event()]))
                for day in (0, 0, 5, 6, 7, 8):
                    result, payload = self.run_day(
                        env, day, self.sources(lambda: SourceFetchResult.partial([])))
                    self.assertEqual(result.run_status, "degraded")
                    self.assertEqual(len(payload["retained_sources"]), 1)
                    retained = payload["retained_sources"][0]
                    self.assertEqual(retained["source_id"], "calendar")
                    self.assertEqual(retained["first_failure_at"], START.isoformat(timespec="seconds"))
                    self.assertEqual(payload["retained_event_count"], int(seed and day < 7))
                _, payload = self.run_day(env, 9, self.sources(lambda: SourceFetchResult.success([])))
                self.assertEqual(payload["retained_sources"], [])

    def test_empty_partial_with_benign_diagnostics_still_tracks_outage(self):
        for kind in ("QualityGateWarning", "OptionalDetailWarning"):
            with self.subTest(kind=kind), make_runner_env() as env:
                self.run_day(env, -1, self.sources(lambda: [event()]))

                def partial():
                    runner.common.log_source_error("Calendar", ValueError("benign diagnostic"), error_type=kind)
                    return SourceFetchResult.partial([])

                for day in (0, 5, 7, 8):
                    _, payload = self.run_day(env, day, self.sources(partial))
                    self.assertEqual(len(payload["retained_sources"]), 1)
                    self.assertEqual(payload["retained_sources"][0]["first_failure_at"],
                                     START.isoformat(timespec="seconds"))
                    self.assertEqual(payload["retained_event_count"], int(day < 7))

                def healthy_empty():
                    partial()  # Same benign diagnostic, but authoritative output.
                    return SourceFetchResult.success([])

                _, payload = self.run_day(env, 9, self.sources(healthy_empty))
                self.assertEqual(payload["retained_sources"], [])
                self.assertNotIn("_explicit_empty_partial", payload["source_results"]["Calendar"])

    def test_child_endpoint_diagnostics_do_not_retain_withdrawn_sibling(self):
        from nrw_events.health import EndpointOutcome

        for message in ("child offline\n  retry later", "child offline " + "ü" * 600):
            for mixed, empty in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(message=message[:30], mixed=mixed, empty=empty), make_runner_env() as env:
                    self.run_day(env, -1, self.sources(lambda: [
                        event("Child", "Child cached"), event("Sibling", "Withdrawn sibling")]))

                    def partial():
                        runner.common.log_source_error("Child", TimeoutError(message))
                        warnings = ("whole runner endpoint failed",) if mixed else ()
                        return SourceFetchResult.partial(
                            [] if empty else [event("Sibling", "Fresh sibling")], *warnings,
                            endpoints=(EndpointOutcome("https://example.test/child",
                                                       error_type="TimeoutError", error=message),))

                    for day in (0, 5, 6, 7, 8):
                        result, payload = self.run_day(env, day, self.sources(partial))
                        self.assertEqual({row["source_id"] for row in payload["retained_sources"]},
                                         {"child", "sibling"} if mixed else {"child"})
                        titles = {e.title for e in result.events}
                        self.assertEqual("Fresh sibling" in titles, not empty)
                        self.assertEqual("Child cached" in titles, day < 7)
                        self.assertEqual("Withdrawn sibling" in titles, mixed and day < 7)

    def test_expired_cancelled_never_become_active(self):
        with make_runner_env() as env:
            self.run_day(env, -1, self.sources(lambda: [event()]))
            _, payload = self.run_day(env, 0, self.sources(lambda: [event(status="cancelled")]))
            result, _ = self.run_day(env, 1, self.sources(failed))
            self.assertFalse(any(e.title == "Concert" and e.status == "scheduled" for e in result.events))
            result, _ = self.run_day(env, 20, self.sources(failed))
            self.assertFalse(any(e.title == "Concert" for e in result.events))

    def test_legacy_failure_counters_do_not_imply_days(self):
        with make_runner_env() as env:
            _, payload = self.run_day(env, -1, self.sources(lambda: [event()]))
            payload["retained_sources"] = [{"source": "Calendar", "source_id": "calendar",
                "runner_source": "Calendar", "last_success_at": "2020-01-01",
                "consecutive_failures": 1000, "retained_event_count": 1, "expired_event_count": 0}]
            env.previous_path.write_text(json.dumps(payload))
            _, payload = self.run_day(env, 0, self.sources(failed))
            self.assertEqual(payload["retained_event_count"], 1)
            self.assertEqual(payload["retained_sources"][0]["first_failure_at"], START.isoformat(timespec="seconds"))

    def test_grouped_failure_tracks_only_failed_child_including_no_data_child(self):
        with make_runner_env() as env:
            def partial():
                runner.common.log_source_error("Child", TimeoutError("offline"))
                runner.common.log_source_error("Empty child", ValueError("parser wrapper malformed"))
                return [event("Sibling", "Fresh sibling")]
            self.run_day(env, -1, self.sources(lambda: [event("Child"), event("Sibling", "Fresh sibling")]))
            for day in (0, 6, 7, 8):
                result, payload = self.run_day(env, day, self.sources(partial))
                self.assertEqual({row["source_id"] for row in payload["retained_sources"]}, {"child", "empty-child"})
                self.assertTrue(all(row["runner_source"] == "Calendar" for row in payload["retained_sources"]))
                self.assertIn("Fresh sibling", {e.title for e in result.events})
                self.assertEqual("Concert" in {e.title for e in result.events}, day < 7)
            _, payload = self.run_day(env, 9, self.sources(lambda: [event("Sibling", "Fresh sibling")]))
            self.assertEqual(payload["retained_sources"], [])

    def test_malformed_records_are_outage_not_healthy_empty(self):
        with make_runner_env() as env:
            self.run_day(env, -1, self.sources(lambda: [event()]))
            malformed = lambda: [42]  # A changed payload contains non-record values.
            for day in (0, 6, 7):
                result, payload = self.run_day(env, day, self.sources(malformed))
                self.assertEqual(result.source_results["Calendar"].status.value, "degraded")
                self.assertEqual(payload["retained_event_count"], int(day < 7))

    def test_scheduled_skip_does_not_start_or_reset_outage(self):
        with make_runner_env() as env:
            self.run_day(env, -1, self.sources(lambda: [event()]))
            skip = lambda: SourceFetchResult.scheduled_skip("weekly")
            _, payload = self.run_day(env, 0, self.sources(skip))
            self.assertEqual(payload["retained_sources"][0]["first_failure_at"], "")
            _, payload = self.run_day(env, 1, self.sources(failed))
            first = payload["retained_sources"][0]["first_failure_at"]
            _, payload = self.run_day(env, 8, self.sources(skip))
            self.assertEqual(payload["retained_sources"][0]["first_failure_at"], first)
            self.assertEqual(payload["retained_event_count"], 0)

    def test_majority_parser_collapse_remains_fatal_with_a_healthy_source(self):
        with make_runner_env() as env:
            sources = self.sources(SourceFetchResult.parser_empty)
            sources["Another failed parser"] = SourceFetchResult.parser_empty
            result, _ = self.run_day(env, 0, sources)
            self.assertEqual(result.run_status, "failed")

    def test_optional_detail_timeout_does_not_create_source_outage(self):
        import time
        from collections import Counter
        from unittest.mock import patch
        from nrw_events import detail_enrichment
        def detail_only():
            row = event(link="https://calendar.bonn.de/event/concert")
            with patch.object(runner.common, "fetch_detail_url", side_effect=TimeoutError("optional detail offline")):
                return detail_enrichment._enrich_batch([row], "offline-test", time.monotonic() + 60,
                                                      {id(row)}, Counter({row["link"]: 1}))
        with make_runner_env() as env:
            for day in (0, 5, 7):
                result, payload = self.run_day(env, day, self.sources(detail_only))
                self.assertIn("Concert", {e.title for e in result.events})
                self.assertEqual(payload["retained_sources"], [])

    def test_optional_endpoint_diagnostics_do_not_create_outage(self):
        from nrw_events import http

        for message in ("optional detail\n  offline", "optional detail " + "ü" * 600):
            with self.subTest(message=message[:30]), make_runner_env() as env:
                def detail_only():
                    url = "https://example.test/detail"
                    http._record_endpoint(url, error_type="TimeoutError", error=message)
                    http._mark_optional_detail_failure(url, TimeoutError(message))
                    runner.common.log_source_error(
                        "Calendar", TimeoutError(message), error_type="OptionalDetailWarning")
                    return SourceFetchResult.partial([event(title="Fresh concert")])

                self.run_day(env, -1, self.sources(lambda: [event()]))
                for day in (0, 5, 7):
                    result, payload = self.run_day(env, day, self.sources(detail_only))
                    self.assertEqual(payload["retained_sources"], [])
                    self.assertNotIn("Concert", {e.title for e in result.events})
                    self.assertIn("Fresh concert", {e.title for e in result.events})

    def test_empty_partial_total_failure_remains_fatal(self):
        with make_runner_env() as env:
            self.run_day(env, -1, self.sources(lambda: [event()]))
            partial = lambda: SourceFetchResult.partial([])
            result, payload = self.run_day(env, 0, {"Calendar": partial, "Healthy": partial})
            self.assertEqual(result.run_status, "failed")
            self.assertEqual(payload["retained_event_count"], 2)

    def test_mixed_child_and_whole_runner_partial_failure_retains_both_cohorts(self):
        with make_runner_env() as env:
            self.run_day(env, -1, self.sources(lambda: [event("Child", "Child cached"), event("Sibling", "Sibling cached")]))
            def partial():
                runner.common.log_source_error("Child", TimeoutError("child unavailable"))
                return SourceFetchResult.partial([event("Sibling", "Fresh sibling")], "whole runner endpoint failed")
            for day in (0, 6, 7):
                result, payload = self.run_day(env, day, self.sources(partial))
                self.assertEqual({row["source_id"] for row in payload["retained_sources"]}, {"child", "sibling"})
                titles = {e.title for e in result.events}
                self.assertIn("Fresh sibling", titles)
                self.assertEqual("Child cached" in titles, day < 7)
                self.assertEqual("Sibling cached" in titles, day < 7)

    def test_large_source_day7_expiry_does_not_disable_global_guard(self):
        with make_runner_env() as env:
            # Distinct numeric occurrence titles are not global dedup twins.
            large = lambda: [event(title=f"Concert episode {i}") for i in range(6)]
            _, payload = self.run_day(env, -1, self.sources(large))
            self.assertEqual(payload["event_count"], 7)
            self.run_day(env, 0, self.sources(failed))
            result, payload = self.run_day(env, 7, self.sources(failed))
            self.assertEqual(result.run_status, "degraded")
            self.assertEqual(payload["event_count"], 1)
            result, _ = self.run_day(env, 8, {"Calendar": failed, "Healthy": failed})
            self.assertEqual(result.run_status, "failed")

    def test_total_failure_remains_fatal_even_with_retention(self):
        with make_runner_env() as env:
            self.run_day(env, -1, self.sources(lambda: [event()]))
            result, _ = self.run_day(env, 0, {"Calendar": failed, "Healthy": failed})
            self.assertEqual(result.run_status, "failed")
