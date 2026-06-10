import unittest
from unittest.mock import Mock, patch

from scripts.nrw_events import common


class HttpHeaderTests(unittest.TestCase):
    def test_fetch_url_uses_browser_like_headers_by_default(self):
        response = Mock()
        response.read.return_value = b"ok"

        with patch("scripts.nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("scripts.nrw_events.common.urllib.request.Request") as request:
            common.fetch_url("https://example.org/events")

        _, kwargs = request.call_args
        headers = kwargs["headers"]
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertNotIn("Python-urllib", headers["User-Agent"])
        self.assertEqual(headers["Accept-Language"], "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7")
        self.assertIn("text/html", headers["Accept"])
        self.assertEqual(headers["Upgrade-Insecure-Requests"], "1")
        self.assertEqual(headers["Sec-Fetch-Mode"], "navigate")

    def test_custom_headers_override_browser_defaults(self):
        response = Mock()
        response.read.return_value = b"ok"

        with patch("scripts.nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("scripts.nrw_events.common.urllib.request.Request") as request:
            common.fetch_url(
                "https://example.org/feed.json",
                headers={"Accept": "application/feed+json", "User-Agent": "Custom Browser"},
            )

        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["Accept"], "application/feed+json")
        self.assertEqual(headers["User-Agent"], "Custom Browser")
        self.assertEqual(headers["Accept-Language"], "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7")

    def test_post_json_keeps_json_headers_with_browser_user_agent(self):
        response = Mock()
        response.read.return_value = b'{"ok": true}'

        with patch("scripts.nrw_events.common.urllib.request.urlopen", return_value=response), \
             patch("scripts.nrw_events.common.urllib.request.Request") as request:
            self.assertEqual(common.post_json("https://example.org/api", {"q": "events"}), {"ok": True})

        headers = request.call_args.kwargs["headers"]
        self.assertIn("Mozilla/5.0", headers["User-Agent"])
        self.assertNotIn("Python-urllib", headers["User-Agent"])
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")


if __name__ == "__main__":
    unittest.main()
