import json
import logging
import os
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest import mock

from nrw_events import common, config, dates, location, scoring
from nrw_events.observability import JsonFormatter
from nrw_events.runtime import EventWindow
from nrw_events.sources import regional_common


class HardeningRegressionTests(unittest.TestCase):
    def test_aware_timestamps_are_normalized_to_berlin_before_becoming_naive(self):
        self.assertEqual(
            dates.parse_iso_date("2026-07-18T23:30:00Z"),
            datetime(2026, 7, 19, 1, 30),
        )
        self.assertEqual(
            dates.parse_date("Sat, 18 Jul 2026 23:30:00 +0000"),
            datetime(2026, 7, 19, 1, 30),
        )

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
