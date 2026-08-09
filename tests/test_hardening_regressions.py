import json
import inspect
import logging
import os
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest import mock

from nrw_events import common, config, dates, location, scoring
from nrw_events.event_builder import EventDraft, build_event
from nrw_events.observability import JsonFormatter
from nrw_events.runtime import EventWindow
from nrw_events.sources import regional_common


class HardeningRegressionTests(unittest.TestCase):
    def test_ambiguous_city_names_require_location_context(self):
        for text in (
            "Wissen für alle", "So much fun", "Linzertorte backen",
            "Geschichte der Grafschaft",
        ):
            with self.subTest(text=text):
                self.assertIsNone(location.guess_city_from_text(text))

        cases = {
            "Vortrag in Wissen": "wissen",
            "Jugendzentrum, Much": "much",
            "53545 Linz": "linz",
            "53501 Grafschaft-Lantershofen": "grafschaft",
            "Linz am Rhein": "linz am rhein",
            "Linz, Marktplatz": "linz",
            "Much, Hauptstraße 12": "much",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(location.guess_city_from_text(text), expected)
    def test_host_slots_share_the_throttle_suffix_across_subdomains(self):
        with mock.patch.dict(common._HOST_THROTTLE_SECONDS_BY_SUFFIX, {"bonn.de": 0.0}, clear=True), \
                mock.patch.dict(common._HOST_SLOTS, {}, clear=True):
            with common._host_request_slot("https://bonn.de/events", time.perf_counter() + 1):
                pass
            with common._host_request_slot("https://www.bonn.de/events", time.perf_counter() + 1):
                pass

            self.assertEqual(set(common._HOST_SLOTS), {"bonn.de"})

    def test_host_slot_rechecks_deadline_after_throttle_wait(self):
        with mock.patch.object(
            common, "_remaining_timeout", side_effect=[1.0, TimeoutError("budget exhausted")],
        ) as remaining, mock.patch.object(common, "_throttle_before_request"):
            with self.assertRaisesRegex(TimeoutError, "budget exhausted"):
                with common._host_request_slot("https://example.test/events", 100.0):
                    self.fail("expired request entered the network section")

        self.assertEqual(remaining.call_count, 2)

    def test_search_results_use_the_bundled_event_pipeline(self):
        with mock.patch.object(common, "TODAY", datetime(2026, 8, 3)), \
                mock.patch.object(common, "END_DATE", datetime(2026, 8, 9)):
            event = common.search_result_event(
                "Album Release-Konzert in Bonn",
                "https://münchen.example/event",
                "5. August 2026, 20 Uhr im Pantheon Bonn.",
                "Exa Search",
                0.58,
            )
        self.assertIsNotNone(event)
        self.assertEqual(event and event["category_key"], "concert")
        self.assertEqual(event and event["link"], "https://xn--mnchen-3ya.example/event")
        self.assertIn("venue_id", event or {})
        self.assertIn("admission_basis", event or {})

    def test_event_draft_is_the_typed_builder_boundary(self):
        event = build_event(EventDraft(
            title="Testkonzert", start=datetime(2026, 8, 5), end=None,
            venue="Pantheon", city="Bonn", description="Live-Musik",
            link="https://example.test/concert", source="Test", category="Konzert",
        ))
        self.assertIsNotNone(event)
        parameters = inspect.signature(common.infer_admission).parameters
        self.assertNotIn("venue", parameters)
        self.assertNotIn("source", parameters)
        self.assertNotIn("link", parameters)

    def test_event_builder_canonicalizes_bad_neuenahr_city_alias(self):
        event = build_event(EventDraft(
            title="Oldtimer-Treffen", start=datetime(2026, 8, 8), end=None,
            venue="", city="Bad Neuenahr", description="Treffen im Kurviertel",
            link="https://example.test/event", source="Ahrtal", category="Kultur",
        ))

        self.assertIsNotNone(event)
        self.assertEqual(event and event["city"], "Bad Neuenahr-Ahrweiler")

    def test_clean_html_removes_complete_comments_with_embedded_angle_brackets(self):
        self.assertEqual(
            common.clean_html("Vorher<!-- internal > metadata -->Nachher"),
            "Vorher Nachher",
        )
        self.assertEqual(common.clean_html("&lt;b&gt;fett&lt;/b&gt;"), "fett")

    def test_aware_timestamps_are_normalized_to_berlin_before_becoming_naive(self):
        self.assertEqual(
            dates.parse_iso_date("2026-07-18T23:30:00Z"),
            datetime(2026, 7, 19, 1, 30),
        )
        self.assertEqual(
            dates.parse_date("Sat, 18 Jul 2026 23:30:00 +0000"),
            datetime(2026, 7, 19, 1, 30),
        )

    def test_yearless_dates_resolve_to_the_next_plausible_occurrence(self):
        self.assertEqual(
            dates.parse_date("So., 3. August", reference_date=datetime(2026, 7, 28)),
            datetime(2026, 8, 3),
        )
        self.assertEqual(
            dates.parse_date("3. Januar", reference_date=datetime(2026, 12, 30)),
            datetime(2027, 1, 3),
        )
        self.assertEqual(
            dates.parse_date("29. Februar", reference_date=datetime(2026, 3, 1)),
            datetime(2028, 2, 29),
        )

    def test_date_ranges_keep_the_start_and_inherit_end_context(self):
        cases = {
            "01.08. - 05.08.2026": datetime(2026, 8, 1),
            "01.08 - 05.08.2026": datetime(2026, 8, 1),
            "01.08.—05.08.2026": datetime(2026, 8, 1),
            "1. bis 5. August 2026": datetime(2026, 8, 1),
            "1. bis zum 5. August 2026": datetime(2026, 8, 1),
            "28. - 30.08.2026": datetime(2026, 8, 28),
            "30. Juli – 2. August 2026": datetime(2026, 7, 30),
            "30. Dezember – 2. Januar 2027": datetime(2026, 12, 30),
            "Dienstag, 14.07.2026": datetime(2026, 7, 14),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(dates.parse_date(value), expected)

    def test_yearless_dates_keep_recent_occurrences_and_expose_resolution_basis(self):
        resolution = dates.resolve_yearless_date(3, 8, datetime(2026, 8, 10))
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution and resolution.value, datetime(2026, 8, 3))
        self.assertEqual(resolution and resolution.basis, "grace-window")
        self.assertEqual(
            dates.parse_date("3. August", reference_date=datetime(2026, 8, 10)),
            datetime(2026, 8, 3),
        )

    def test_regional_yearless_leap_day_continues_to_the_next_valid_year(self):
        with mock.patch.object(common, "TODAY", datetime(2027, 1, 10)):
            self.assertEqual(regional_common.date_for_window(29, 2), datetime(2028, 2, 29))

    def test_common_date_parser_uses_the_configured_report_window_start(self):
        previous = dates._REFERENCE_DATE
        self.addCleanup(setattr, dates, "_REFERENCE_DATE", previous)
        dates.configure_reference_date(datetime(2026, 12, 30))
        self.assertEqual(common.parse_date("3. Januar"), datetime(2027, 1, 3))

    def test_date_formats_use_explicit_prefixes_and_shared_month_aliases(self):
        self.assertEqual(dates.parse_date("2026-08-03 extra"), datetime(2026, 8, 3))
        self.assertEqual(dates.parse_date("3. Sept 2026"), datetime(2026, 9, 3))
        self.assertEqual(dates.parse_date("3. Sept. 2026"), datetime(2026, 9, 3))
        self.assertEqual(dates.parse_date("4. Okt. 2026"), datetime(2026, 10, 4))
        self.assertIsNone(dates.parse_date("3. Mae 2026"))

    def test_late_artifact_time_rounding_never_wraps_to_same_day_midnight(self):
        self.assertEqual(common.normalize_time_fields("23:53 - 23:59"), ("23:45", ""))

    def test_parse_failures_are_attributed_to_the_active_source(self):
        with mock.patch.object(common, "log_source_error") as log_error:
            self.assertEqual(common.extract_json_array("not-json"), [])
            self.assertEqual(
                common.jsonld_event_items(
                    '<script type="application/ld+json">{not-json}</script>'
                ),
                [],
            )
            parsed = common._ical_parse_dt(
                "20260803T120000", "DTSTART;TZID=Unknown/Nowhere"
            )

        self.assertEqual(parsed, datetime(2026, 8, 3, 12))
        self.assertEqual(
            [call.args[0] for call in log_error.call_args_list],
            ["Search JSON response", "JSON-LD", "iCal timezone"],
        )

    def test_jsonld_ignores_empty_blocks_and_accepts_literal_control_characters(self):
        html = """
        <script type="application/ld+json"> </script>
        <script type="application/ld+json">(()=> consentManager())()</script>
        <script type="application/ld+json">
          {"@type":"Event","name":"Line one
          line two"}
        </script>
        """
        with mock.patch.object(common, "log_source_error") as log_error:
            items = common.jsonld_event_items(html)

        self.assertEqual(len(items), 1)
        self.assertIn("line two", items[0]["name"])
        log_error.assert_not_called()

    def test_malformed_ical_timezone_keeps_the_naive_timestamp(self):
        with mock.patch.object(common, "log_source_error") as log_error:
            parsed = common._ical_parse_dt(
                "20260803T120000", "DTSTART;TZID=/Europe/Berlin"
            )

        self.assertEqual(parsed, datetime(2026, 8, 3, 12))
        log_error.assert_called_once()
        self.assertEqual(log_error.call_args.args[0], "iCal timezone")

    def test_runtime_window_uses_the_berlin_calendar_day(self):
        window = EventWindow.from_days(
            2, datetime(2026, 7, 18, 23, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(window.start, datetime(2026, 7, 19))
        self.assertEqual(window.end, datetime(2026, 7, 20))

    def test_json_log_timestamp_is_explicit_utc(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "ok", (), None)
        record.created = 0
        self.assertEqual(
            json.loads(JsonFormatter().format(record))["timestamp"],
            "1970-01-01T00:00:00Z",
        )

    def test_window_includes_the_last_day_and_rejects_end_only_records(self):
        with mock.patch.object(common, "TODAY", datetime(2026, 7, 19)), mock.patch.object(
            common, "END_DATE", datetime(2026, 7, 20)
        ):
            self.assertTrue(common.window_contains(datetime(2026, 7, 20, 23, 59)))
            self.assertIsNone(
                common.make_event(
                    "End only", None, datetime(2026, 7, 20), "", "Bonn", "",
                    "https://example.test/end-only", "Test", "concert",
                )
            )

    def test_time_listing_resolves_root_relative_links(self):
        html = (
            '<time datetime="2026-07-20T19:00:00">20.07.</time>'
            '<a href="/events/jazzabend">Jazzabend im Park</a>'
        )
        with mock.patch.object(common, "TODAY", datetime(2026, 7, 19)), mock.patch.object(
            common, "END_DATE", datetime(2026, 7, 21)
        ):
            events = common.events_from_time_listing(
                html, "Test", "Bonn", "concert", 1.0, "https://example.test/calendar/"
            )
        self.assertEqual(events[0]["link"], "https://example.test/events/jazzabend")

    def test_http_response_is_closed_when_content_type_validation_fails(self):
        response = mock.Mock()
        headers = Message()
        headers["Content-Type"] = "text/html"
        response.headers = headers
        with mock.patch.object(common.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(common.UnexpectedContentTypeError):
                common.fetch_url(
                    "https://example.test/data",
                    expected_content_types=("application/json",),
                )
        response.close.assert_called_once()

    def test_throttle_reservations_are_independent_per_host_bucket(self):
        delays = {"a.test": 2.0, "b.test": 2.0}
        with mock.patch.object(common, "_HOST_THROTTLE_SECONDS_BY_SUFFIX", delays), \
                mock.patch.object(common, "_HOST_LAST_FETCH_AT", {}), \
                mock.patch.object(common.time, "monotonic", side_effect=[10.0, 10.0, 10.0]), \
                mock.patch.object(common.time, "sleep") as sleep:
            common._throttle_before_request("https://a.test/one")
            common._throttle_before_request("https://a.test/two")
            common._throttle_before_request("https://b.test/one")
        sleep.assert_called_once_with(2.0)

    def test_request_slots_allow_different_hosts_to_run_in_parallel(self):
        rendezvous = threading.Barrier(2, timeout=1)

        def occupy(url):
            with common._host_request_slot(url, common.time.perf_counter() + 2):
                rendezvous.wait()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(occupy, "https://one.test/events"),
                pool.submit(occupy, "https://two.test/events"),
            ]
            for future in futures:
                future.result()

    def test_request_slots_serialize_the_same_host(self):
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first_request():
            with common._host_request_slot("https://same.test/one", common.time.perf_counter() + 2):
                first_entered.set()
                release_first.wait(1)

        def second_request():
            first_entered.wait(1)
            with common._host_request_slot("https://same.test/two", common.time.perf_counter() + 2):
                second_entered.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_request)
            second = pool.submit(second_request)
            self.assertFalse(second_entered.wait(0.05))
            release_first.set()
            first.result()
            second.result()
        self.assertTrue(second_entered.is_set())

    def test_retry_sleep_never_runs_past_the_request_budget(self):
        with mock.patch.object(common.time, "perf_counter", return_value=100.0), \
                mock.patch.object(common.time, "sleep") as sleep:
            with self.assertRaisesRegex(TimeoutError, "budget exhausted"):
                common._sleep_for_retry(3.0, 102.0)
        sleep.assert_not_called()

    def test_socket_timeout_shrinks_to_the_remaining_budget(self):
        with mock.patch.object(common.time, "perf_counter", return_value=100.0):
            self.assertEqual(common._remaining_timeout(106.0, 15.0), 6.0)

    def test_source_deadline_caps_each_request_budget(self):
        with mock.patch.object(common.time, "perf_counter", side_effect=[100.0, 101.0]):
            common.set_source_context(object(), timeout_seconds=5.0)
            try:
                self.assertEqual(common._request_deadline(), 105.0)
            finally:
                common.set_source_context(None)

    def test_successful_endpoint_never_renews_past_the_source_hard_deadline(self):
        result = mock.Mock()
        with mock.patch.object(common.time, "perf_counter", side_effect=[100.0, 120.0, 121.0]):
            common.set_source_context(result, timeout_seconds=30.0)
            try:
                common._record_endpoint("https://progress.test/events", status=200)
                self.assertEqual(common._request_deadline(), 130.0)
            finally:
                common.set_source_context(None)

        result.endpoint.assert_called_once_with("https://progress.test/events", status=200)

    def test_live_memory_cache_rechecks_ttl_and_flushes_once(self):
        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ,
            {"NRW_EVENTS_CACHE_DIR": cache_dir, "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "1"},
        ):
            common._DETAIL_PAGE_CACHE_STATES.clear()
            with mock.patch.object(common.time, "time", return_value=100.0) as clock, \
                    mock.patch.object(common, "fetch_url", side_effect=["old", "new"]) as fetch:
                self.assertEqual(
                    common.fetch_detail_url("https://example.test/detail", cache_namespace="ttl"),
                    "old",
                )
                clock.return_value = 3701.0
                self.assertEqual(
                    common.fetch_detail_url("https://example.test/detail", cache_namespace="ttl"),
                    "new",
                )
            self.assertEqual(fetch.call_count, 2)

            with mock.patch.object(common, "_persist_detail_page_cache") as persist, \
                    mock.patch.object(common, "fetch_url", side_effect=["a", "b"]):
                common.fetch_detail_url("https://example.test/a", cache_namespace="batch")
                common.fetch_detail_url("https://example.test/b", cache_namespace="batch")
                persist.assert_not_called()
                common.flush_detail_page_caches("batch")
                persist.assert_called_once()
            common._DETAIL_PAGE_CACHE_STATES.clear()

    def test_ical_recurrence_expands_rdate_and_exdate_inside_window(self):
        payload = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Sommerkonzert
DTSTART:20260719T180000
DTEND:20260719T200000
RRULE:FREQ=DAILY;COUNT=4
EXDATE:20260720T180000
RDATE:20260724T180000
END:VEVENT
END:VCALENDAR"""
        with mock.patch.object(common, "TODAY", datetime(2026, 7, 19)), mock.patch.object(
            common, "END_DATE", datetime(2026, 7, 25)
        ), mock.patch.object(common, "fetch_url", return_value=payload):
            events = common.fetch_ical(
                "https://example.test/events.ics", "Test", "Bonn", "concert"
            )
        self.assertEqual(
            [event["start_date"] for event in events],
            ["2026-07-19", "2026-07-21", "2026-07-22", "2026-07-24"],
        )

    def test_ical_date_only_exdate_excludes_the_whole_day(self):
        payload = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Sommerkonzert
DTSTART:20260804T200000
DTEND:20260804T220000
RRULE:FREQ=WEEKLY;COUNT=3
EXDATE;VALUE=DATE:20260811
END:VEVENT
END:VCALENDAR"""
        with mock.patch.object(common, "TODAY", datetime(2026, 8, 4)), mock.patch.object(
            common, "END_DATE", datetime(2026, 8, 31)
        ), mock.patch.object(common, "fetch_url", return_value=payload):
            events = common.fetch_ical(
                "https://example.test/events.ics", "Test", "Bonn", "concert"
            )
        self.assertEqual(
            [event["start_date"] for event in events],
            ["2026-08-04", "2026-08-18"],
        )

    def test_unsupported_recurrence_is_visible_as_a_source_warning(self):
        payload = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Sommerkonzert
DTSTART:20260719T180000
RRULE:FREQ=YEARLY
END:VEVENT
END:VCALENDAR"""
        with mock.patch.object(common, "TODAY", datetime(2026, 7, 19)), mock.patch.object(
            common, "END_DATE", datetime(2026, 7, 25)
        ), mock.patch.object(common, "fetch_url", return_value=payload), mock.patch.object(
            common, "log_source_error"
        ) as warning:
            common.fetch_ical("https://example.test/events.ics", "Test", "Bonn", "concert")
        self.assertIn("unsupported RRULE frequency", str(warning.call_args.args[1]))

    def test_class_scoped_parser_treats_void_elements_as_non_nesting(self):
        parser = regional_common.ClassScopedTextParser({
            "copy": lambda _tag, attrs: attrs.get("class") == "copy",
        })
        parser.feed('<div class="copy">Before<hr>After<br>Still here</div><p>Outside</p>')
        self.assertEqual(parser.text("copy"), "Before After Still here")

    def test_cwd_dotenv_is_not_an_implicit_configuration_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, ".env").write_text("NRW_EVENTS_DAYS_AHEAD=89\n")
            previous = os.getcwd()
            try:
                os.chdir(tmpdir)
                with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                    Path,
                    "is_file",
                    autospec=True,
                    side_effect=lambda path: path == Path(tmpdir, ".env"),
                ):
                    self.assertIsNone(config.load_env_file())
                    self.assertEqual(config.runtime_config().days_ahead, 3)
            finally:
                os.chdir(previous)

    def test_default_state_path_respects_xdg_state_home(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/state"}, clear=True):
            self.assertEqual(config.default_state_dir(), Path("/state/nrw-events"))

    def test_scoring_and_location_helpers_have_direct_boundary_coverage(self):
        self.assertEqual(scoring.distance_score(0), 1.0)
        self.assertEqual(scoring.distance_score(config.MAX_RADIUS_KM), 0.1)
        coordinates, confidence, source = location.resolve_location("Bonn")
        self.assertEqual(coordinates, (config.BONN_LAT, config.BONN_LON))
        self.assertEqual((confidence, source), ("known_city", "configured_city"))
        self.assertEqual(
            location.resolve_location("Unknown place"),
            (None, "unresolved", "unknown_city"),
        )
        self.assertIsNone(dates.parse_date("not a date"))

    def test_offline_suite_blocks_direct_socket_connections(self):
        with socket.socket() as candidate:
            with self.assertRaisesRegex(AssertionError, "offline test suite"):
                candidate.connect(("127.0.0.1", 9))


if __name__ == "__main__":
    unittest.main()
