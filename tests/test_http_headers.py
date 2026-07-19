import unittest
import urllib.error
from email.message import Message
from unittest.mock import Mock, patch

from nrw_events import common


class HttpHeaderTests(unittest.TestCase):
    def test_fetch_url_preserves_mixed_utf8_and_windows_1252_characters(self):
        response = Mock()
        response.read.return_value = "Kölner ".encode("utf-8") + b"Flohm\xe4rkte"
        headers = Message()
        headers["Content-Type"] = "text/html; charset=UTF-8"
        response.headers = headers

        with patch("nrw_events.common.urllib.request.urlopen", return_value=response):
            text = common.fetch_url("https://example.org/legacy-events")

        self.assertEqual(text, "Kölner Flohmärkte")

    def test_fetch_url_rejects_oversized_responses(self):
        response = Mock()
        response.read.return_value = b"x" * 11
        old_limit = common._HTTP_MAX_RESPONSE_BYTES
        common._HTTP_MAX_RESPONSE_BYTES = 10
        try:
            with patch("nrw_events.common.urllib.request.urlopen", return_value=response):
                with self.assertRaises(common.ResponseTooLargeError):
                    common.fetch_url("https://example.org/events")
        finally:
            common._HTTP_MAX_RESPONSE_BYTES = old_limit

    def test_fetch_url_validates_expected_content_type(self):
        response = Mock()
        response.read.return_value = b"{}"
        headers = Message()
        headers["Content-Type"] = "text/html"
        response.headers = headers
        with patch("nrw_events.common.urllib.request.urlopen", return_value=response):
            with self.assertRaises(common.UnexpectedContentTypeError):
                common.fetch_url("https://example.org/events", expected_content_types=("application/json",))

    def test_post_json_retries_only_when_marked_safe(self):
        response = Mock()
        response.read.return_value = b'{"ok": true}'
        transient = urllib.error.HTTPError("https://example.org/api", 503, "Unavailable", Message(), None)
        self.addCleanup(transient.close)
        with patch("nrw_events.common.urllib.request.urlopen", side_effect=[transient, response]) as urlopen, \
                patch("nrw_events.common.time.sleep"):
            self.assertEqual(common.post_json("https://example.org/api", {}, retry_safe=True), {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
    def test_fetch_url_uses_browser_like_headers_by_default(self):
        response = Mock()
        response.read.return_value = b"ok"

        with patch("nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("nrw_events.common.urllib.request.Request") as request:
            common.fetch_url("https://example.org/events")

        _, kwargs = request.call_args
        headers = kwargs["headers"]
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertIn("Chrome/131.0.0.0", headers["User-Agent"])
        self.assertIn('v="131"', headers["Sec-CH-UA"])
        self.assertNotIn("Python-urllib", headers["User-Agent"])
        self.assertEqual(headers["Accept-Language"], "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7")
        self.assertIn("text/html", headers["Accept"])
        self.assertEqual(headers["Upgrade-Insecure-Requests"], "1")
        self.assertEqual(headers["Sec-Fetch-Mode"], "navigate")

    def test_fetch_url_can_request_non_html_data(self):
        response = Mock()
        response.read.return_value = b"{}"

        with patch("nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("nrw_events.common.urllib.request.Request") as request:
            common.fetch_url(
                "https://example.org/events.json",
                accept="application/json,*/*;q=0.8",
                sec_fetch_mode="cors",
                sec_fetch_dest="empty",
            )

        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["Accept"], "application/json,*/*;q=0.8")
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(headers["Sec-Fetch-Dest"], "empty")
        self.assertNotIn("Upgrade-Insecure-Requests", headers)

    def test_custom_headers_override_browser_defaults(self):
        response = Mock()
        response.read.return_value = b"ok"

        with patch("nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("nrw_events.common.urllib.request.Request") as request:
            common.fetch_url(
                "https://example.org/feed.json",
                headers={"Accept": "application/feed+json", "User-Agent": "Custom Browser"},
            )

        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["Accept"], "application/feed+json")
        self.assertEqual(headers["User-Agent"], "Custom Browser")
        self.assertEqual(headers["Accept-Language"], "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7")

    def test_fetch_ical_requests_calendar_data_not_html(self):
        response = Mock()
        response.read.return_value = b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"

        with patch("nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("nrw_events.common.urllib.request.Request") as request:
            self.assertEqual(common.fetch_ical("https://example.org/events.ics", "Example", "Bonn"), [])

        headers = request.call_args.kwargs["headers"]
        self.assertIn("text/calendar", headers["Accept"])
        self.assertNotIn("text/html", headers["Accept"])
        self.assertEqual(headers["Sec-Fetch-Mode"], "no-cors")
        self.assertEqual(headers["Sec-Fetch-Dest"], "empty")

    def test_fetch_url_retries_transient_http_errors(self):
        response = Mock()
        response.read.return_value = b"ok after retry"
        transient = urllib.error.HTTPError(
            "https://example.org/events", 503, "Service Temporarily Unavailable", Message(), None)
        self.addCleanup(transient.close)

        with patch("nrw_events.common.urllib.request.urlopen", side_effect=[transient, response]) as urlopen, \
             patch("nrw_events.common.time.sleep") as sleep:
            self.assertEqual(common.fetch_url("https://example.org/events"), "ok after retry")

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_fetch_url_does_not_retry_non_transient_http_errors(self):
        not_found = urllib.error.HTTPError(
            "https://example.org/missing", 404, "Not Found", Message(), None)
        self.addCleanup(not_found.close)

        with patch("nrw_events.common.urllib.request.urlopen", side_effect=not_found) as urlopen, \
             patch("nrw_events.common.time.sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                common.fetch_url("https://example.org/missing")

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_fetch_url_throttles_bonn_de_requests(self):
        response = Mock()
        response.read.return_value = b"ok"
        old_delay = common._HOST_THROTTLE_SECONDS_BY_SUFFIX["bonn.de"]
        common._HOST_THROTTLE_SECONDS_BY_SUFFIX["bonn.de"] = 1.0
        common._HOST_LAST_FETCH_AT.clear()
        common._HOST_LAST_FETCH_AT["bonn.de"] = 100.0

        try:
            with patch("nrw_events.common.urllib.request.urlopen", return_value=response), \
                 patch("nrw_events.common.time.monotonic", side_effect=[100.25, 101.0]), \
                 patch("nrw_events.common.time.sleep") as sleep:
                self.assertEqual(common.fetch_url("https://www.bonn.de/citykey/events-json.php"), "ok")
        finally:
            common._HOST_LAST_FETCH_AT.clear()
            common._HOST_THROTTLE_SECONDS_BY_SUFFIX["bonn.de"] = old_delay

        sleep.assert_called_once_with(0.75)

    def test_post_json_keeps_json_headers_with_browser_user_agent(self):
        response = Mock()
        response.read.return_value = b'{"ok": true}'

        with patch("nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("nrw_events.common.urllib.request.Request") as request:
            self.assertEqual(common.post_json("https://example.org/api", {"q": "events"}), {"ok": True})

        headers = request.call_args.kwargs["headers"]
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertNotIn("Python-urllib", headers["User-Agent"])
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")

    def test_post_form_encodes_fields_and_uses_browser_headers(self):
        response = Mock()
        response.read.return_value = b'{"data": {"content": "ok"}}'

        with patch("nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("nrw_events.common.urllib.request.Request") as request:
            payload = common.post_form(
                "https://example.org/events-api",
                [("filter", "music"), ("category", "1"), ("category", "2")],
                headers={"Referer": "https://example.org/events"},
            )

        self.assertEqual(payload, {"data": {"content": "ok"}})
        self.assertEqual(
            request.call_args.kwargs["data"],
            b"filter=music&category=1&category=2",
        )
        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["Content-Type"], "application/x-www-form-urlencoded")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Referer"], "https://example.org/events")


if __name__ == "__main__":
    unittest.main()
