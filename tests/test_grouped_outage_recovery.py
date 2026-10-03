"""Same-generation recorded health replay and grouped child recovery contracts."""
import json
import unittest
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from nrw_events import common, http, runner
from nrw_events.health import SourceResult, SourceStatus
from nrw_events.retention_policy import _retain_previous_events, _retention_labels
from nrw_events.sources import bonn_venues
from nrw_events.validation import EventValidationError, validate_event

from tests import test_source_outage as outage
from tests.helpers import make_runner_env, patch_window
from tests.test_source_outage import START, event

NAME = "Bonn venue calendars"
API = bonn_venues._BROTFABRIK_EVENTS_API
FIXTURE = Path(__file__).parent / "data/bonn-venue-health-20261003.json"


def recorded_result(payload):
    row = payload["source_results"][NAME]
    keys = {field.name for field in fields(SourceResult)} - {"source", "status", "source_id"}
    return SourceResult(NAME, source_id=payload["source_id"], status=SourceStatus(row["status"]),
                        **{key: value for key, value in row.items() if key in keys})


class GroupedOutageRecoveryTests(unittest.TestCase):
    def test_recorded_same_generation_healthy_child_clears_false_episodes(self):
        payload = json.loads(FIXTURE.read_text())
        self.assertEqual(payload["run_id"], payload["composed_run_id"])
        self.assertEqual(payload["generated_at"], payload["composed_generated_at"])
        self.assertEqual(payload["composed_brotfabrik_count"], 40)
        result = recorded_result(payload)
        self.assertEqual(result.rejection_reasons,
                         {"quality:civic.course": 8, "link_invalid": 1, "filter:score_floor": 2})
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.endpoints[API], {
            "attempts": 1, "status": 200, "content_type": "application/json",
            "bytes": 125986, "duration_ms": 190})
        prior = next(row for row in payload["retained_sources"]
                     if row["source_id"] == "brotfabrik-bonn-api")
        self.assertEqual(prior["consecutive_failures"], 17)
        self.assertGreater(datetime.fromisoformat(payload["generated_at"]) -
                           datetime.fromisoformat(prior["first_failure_at"]), timedelta(days=5))
        with make_runner_env() as env:
            now = datetime.fromisoformat(payload["generated_at"])
            context = env.context(clock=lambda: now)
            # Even with no remaining cached inventory, remove the persisted clock.
            retained, summary = _retain_previous_events({NAME: result}, payload, context)
        self.assertFalse(result.has_outage_evidence())
        self.assertEqual(retained, [])
        self.assertEqual(summary["retained_sources"], [])
        # Degraded diagnostics remain visible; recovery is not status laundering.
        self.assertEqual(result.status, SourceStatus.DEGRADED)

    def test_failed_sibling_does_not_reopen_recovered_legacy_api_episode(self):
        payload = json.loads(FIXTURE.read_text())
        result = recorded_result(payload)
        result.warning("KULT41", "TimeoutError", "listing unavailable", source_id="kult41")
        result.endpoint("https://www.kult41.de/veranstaltungen/programm?mo=10&yr=2026",
                        error_type="TimeoutError", error="listing unavailable")
        self.assertEqual(_retention_labels({NAME: result}, payload), {"kult41"})

    def test_link_rejection_does_not_retain_withdrawn_sibling_or_create_episode(self):
        case = outage.SourceOutageTests()
        with make_runner_env() as env:
            sources = lambda fetch: {"Calendar": fetch, "Healthy": lambda: [event("Healthy", "Play")]}
            case.run_day(env, -1, sources(lambda: [event("Child", "Withdrawn child")]))
            def fetch():
                return [event("Sibling", "Fresh sibling"),
                        event("Child", "Invalid link", link="/relative/event")]
            result, payload = case.run_day(env, 0, sources(fetch))
            self.assertEqual(payload["retained_sources"], [])
            self.assertEqual([row.title for row in result.events], ["Fresh sibling", "Play"])
            self.assertEqual(result.source_results["Calendar"].rejection_reasons, {"link_invalid": 1})

    def test_recovery_then_real_failure_starts_new_clock(self):
        case = outage.SourceOutageTests()
        with make_runner_env() as env:
            sources = lambda fetch: {"Calendar": fetch, "Healthy": lambda: [event("Healthy", "Play")]}
            case.run_day(env, 0, sources(lambda: runner.SourceFetchResult.parser_empty()))
            _, recovered = case.run_day(env, 5, sources(lambda: [
                event(), event(title="Rejected link", link="/relative/event")]))
            self.assertEqual(recovered["retained_sources"], [])
            _, failed = case.run_day(env, 6, sources(lambda: runner.SourceFetchResult.parser_empty()))
            self.assertEqual(failed["retained_sources"][0]["first_failure_at"],
                             (START + timedelta(days=6)).isoformat(timespec="seconds"))

    def api_result(self, body):
        result = SourceResult(NAME, source_id="bonn-venue-calendars")
        def fetch(*args, **kwargs):
            http._record_endpoint(API, status=200, content_type="application/json", bytes=len(body))
            return body
        common.set_source_context(result)
        try:
            with patch.object(common, "fetch_url", side_effect=fetch), \
                 patch.object(bonn_venues.rc, "fetch_html_events", return_value=[]) as fallback:
                rows = bonn_venues._fetch_brotfabrik()
                result.finish(rows)
                return result, rows, fallback.called
        finally:
            common.set_source_context(None)

    def test_api_valid_empty_is_authoritative_and_recovers_no_inventory(self):
        result, rows, fallback = self.api_result("[]")
        self.assertEqual(rows, [])
        self.assertFalse(fallback)
        self.assertEqual(result.status, SourceStatus.HEALTHY_EMPTY)
        self.assertIs(result.endpoints[API]["parser_empty"], False)
        self.assertEqual(result.endpoints[API]["source_id"], "brotfabrik-bonn")
        payload = json.loads(FIXTURE.read_text())
        self.assertEqual(_retention_labels({NAME: result}, payload), set())

    def test_optional_endpoint_and_link_rejection_do_not_keep_old_episode(self):
        payload = json.loads(FIXTURE.read_text())
        result = recorded_result(payload)
        result.warning("Brotfabrik Bonn detail", "OptionalDetailWarning", "detail unavailable",
                       source_id="brotfabrik-bonn")
        result.endpoint("https://brotfabrik-bonn.de/detail", source_id="brotfabrik-bonn",
                        error_type="TimeoutError", error="detail unavailable", optional_detail=True)
        self.assertFalse(result.has_outage_evidence())
        self.assertEqual(_retention_labels({NAME: result}, payload), set())

    def test_optional_detail_parser_empty_does_not_turn_authoritative_empty_into_outage(self):
        case = outage.SourceOutageTests()
        with make_runner_env() as env:
            sources = lambda fetch: {"Calendar": fetch, "Healthy": lambda: [event("Healthy", "Play")]}
            case.run_day(env, 0, sources(lambda: runner.SourceFetchResult.parser_empty()))
            def healthy_empty():
                http._record_endpoint("https://example.test/optional-detail", parser_type="html",
                                      parser_empty=True, optional_detail=True)
                return runner.SourceFetchResult.success([])
            result, payload = case.run_day(env, 5, sources(healthy_empty))
            self.assertFalse(result.source_results["Calendar"].has_outage_evidence())
            self.assertEqual(payload["retained_sources"], [])

    def test_majority_link_rejections_do_not_waive_empty_or_drop_guards(self):
        case = outage.SourceOutageTests()
        with make_runner_env() as env:
            sources = {"A": lambda: [event("A", "Concert A")],
                       "B": lambda: [event("B", "Concert B")],
                       "C": lambda: [event("C", "Concert C")]}
            case.run_day(env, -1, sources)
            result, payload = case.run_day(env, 0, {
                "A": lambda: [event("A", "Rejected A", link="/relative")],
                "B": lambda: [event("B", "Rejected B", link="/relative")],
                "C": sources["C"],
            })
            self.assertEqual(result.run_status, "failed")
            self.assertEqual(payload["retained_sources"], [])
            result, _ = case.run_day(env, 1, {
                "A": lambda: [event("A", "Rejected A", link="/relative")],
            })
            self.assertEqual(result.run_status, "failed")

    def test_api_offwindow_and_editorial_only_cohorts_are_not_parser_empty(self):
        patch_window(self, datetime(2026, 9, 15), datetime(2026, 10, 12))
        for items in ([{"Titel": "Concert", "Datum": "2026-08-01", "Gewerk": "Musik"}],
                      [{"Titel": "Deutschkurs für Männer", "Datum": "2026-09-20", "Gewerk": "Kurs"}]):
            with self.subTest(items=items):
                result, rows, fallback = self.api_result(json.dumps(items))
                # Adapter candidates precede the canonical publication window
                # and editorial boundary; neither rejection is parser drift.
                if items[0]["Datum"] == "2026-08-01":
                    self.assertEqual(len(rows), 1)
                    self.assertFalse(common.event_in_window(rows[0]))
                    self.assertEqual(result.endpoints[API]["out_of_window_count"], 1)
                elif rows:
                    with self.assertRaisesRegex(EventValidationError, "quality:civic.course"):
                        validate_event(rows[0])
                self.assertFalse(fallback)
                self.assertFalse(result.has_outage_evidence())
                self.assertIs(result.endpoints[API]["parser_empty"], False)
                self.assertEqual(result.endpoints[API]["candidate_count"], 1)

    def test_api_good_records_are_not_lost_to_one_unparseable_title(self):
        patch_window(self, datetime(2026, 9, 15), datetime(2026, 10, 12))
        result, rows, fallback = self.api_result(json.dumps([
            {"Titel": "Concert", "Datum": "2026-09-20", "Gewerk": "Musik"},
            {"Datum": "2026-09-20"},
        ]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_id"], "brotfabrik-bonn")
        self.assertFalse(fallback)
        self.assertFalse(result.has_outage_evidence())

    def test_http200_malformed_api_is_real_child_parser_outage(self):
        for body in ('{"changed_wrapper": []}', '[{"ChangedTitle": "Concert"}]'):
            with self.subTest(body=body):
                result, rows, fallback = self.api_result(body)
                self.assertEqual(rows, [])
                self.assertTrue(fallback)
                self.assertTrue(result.has_outage_evidence())
                self.assertEqual(result.endpoints[API]["status"], 200)
                self.assertIs(result.endpoints[API]["parser_empty"], True)
                self.assertEqual({warning["source_id"] for warning in result.warnings}, {"brotfabrik-bonn"})
                self.assertEqual(_retention_labels({NAME: result}, json.loads(FIXTURE.read_text())),
                                 {"brotfabrik-bonn"})

    def test_endpoint_attributed_parser_failure_without_warning_retains_only_child(self):
        payload = json.loads(FIXTURE.read_text())
        result = recorded_result(payload)
        result.endpoint(API, source_id="brotfabrik-bonn", parser_empty=True)
        self.assertEqual(_retention_labels({NAME: result}, payload), {"brotfabrik-bonn"})

    def test_continuing_failure_migrates_legacy_episode_without_restarting_clock(self):
        result, _, _ = self.api_result('{"changed_wrapper": []}')
        payload = json.loads(FIXTURE.read_text())
        legacy = next(row for row in payload["retained_sources"]
                      if row["source_id"] == "brotfabrik-bonn-api")
        # Both historical identities coexist in the recorded snapshot. Keep
        # earliest observed failure, not a new clock caused by the rename.
        with make_runner_env() as env:
            now = datetime.fromisoformat(payload["generated_at"])
            _, summary = _retain_previous_events({NAME: result}, payload, env.context(clock=lambda: now))
        self.assertEqual(len(summary["retained_sources"]), 1)
        episode = summary["retained_sources"][0]
        self.assertEqual(episode["source_id"], "brotfabrik-bonn")
        self.assertEqual(episode["first_failure_at"], legacy["first_failure_at"])
        self.assertEqual(episode["consecutive_failures"], legacy["consecutive_failures"] + 1)

    def test_real_runner_failure_with_child_failure_still_protects_siblings(self):
        payload = json.loads(FIXTURE.read_text())
        result = recorded_result(payload)
        result.endpoint(API, source_id="brotfabrik-bonn", parser_empty=True)
        result.warning(NAME, "SourceWarning", "whole runner partial result", source_id=result.source_id)
        labels = _retention_labels({NAME: result}, payload)
        self.assertIn("brotfabrik-bonn", labels)
        self.assertIn("kult41", labels)
        self.assertIn("repair-cafes-bonn", labels)
