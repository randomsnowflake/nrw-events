"""kdvz botgate: once-per-process solve, persisted cookie, day-scoped cache."""

import hashlib
import io
import os
import tempfile
import time
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from nrw_events import botgate, detail_cache, http


def _challenge(token: str = "k1.test-token", difficulty: int = 2) -> str:
    return (
        "<html><title>Einen Moment bitte</title><script>(function () {\n"
        f'  var token = "{token}", difficulty =  {difficulty} , '
        'verifyURL = "/.well-known/botgate/verify", redirect = "/kalender.php";\n'
        "})();</script></html>"
    )


class _Response(io.BytesIO):
    def __init__(self, body: str, content_type: str = "text/html; charset=utf-8", status: int = 200):
        super().__init__(body.encode("utf-8"))
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _redirect_with_cookie(url: str, cookie: str = "botgate=abc123; Max-Age=604800; Path=/; Domain=.bonn.de"):
    headers = Message()
    headers["Set-Cookie"] = cookie
    headers["Location"] = "/kalender.php"
    return urllib.error.HTTPError(url, 302, "Found", headers, io.BytesIO(b""))


class BotgateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {
            "NRW_EVENTS_CACHE_DIR": self._tmp.name,
            "NRW_EVENTS_GATED_RESPONSE_TTL_HOURS": "20",
            "NRW_EVENTS_HTTP_RETRY_ATTEMPTS": "1",
        })
        self._env.start()
        botgate.reset_for_tests()
        self._sleep = patch.object(http.time, "sleep")
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        self._env.stop()
        botgate.reset_for_tests()
        self._tmp.cleanup()

    def test_gated_bucket_matches_portals_but_not_lookalike_domains(self):
        self.assertEqual(botgate.gated_bucket("https://www.bonn.de/x.php"), "bonn.de")
        self.assertEqual(botgate.gated_bucket("https://stadtplan.bonn.de/"), "bonn.de")
        self.assertEqual(botgate.gated_bucket("https://www.huerth.de/events/a.php"), "huerth.de")
        self.assertIsNone(botgate.gated_bucket("https://www.uni-bonn.de/de/veranstaltungen"))
        self.assertIsNone(botgate.gated_bucket("https://www.kunstmuseum-bonn.de/"))

    def test_solver_matches_browser_worker_definition(self):
        nonce = botgate.solve("k1.abc", 12)
        digest = hashlib.sha256(f"k1.abc:{nonce}".encode()).digest()
        self.assertEqual(int.from_bytes(digest[:4], "big") >> 20, 0)

    def test_solver_refuses_impolite_difficulty(self):
        with self.assertRaises(botgate.BotgateError):
            botgate.solve("k1.abc", 28)

    def test_challenge_is_solved_once_cookie_persisted_and_page_cached(self):
        url = "https://www.bonn.de/kalender.php"
        calls = []

        def urlopen(request, timeout=None):
            calls.append(request.headers.get("Cookie"))
            if request.headers.get("Cookie") == "botgate=abc123":
                return _Response("<html>events</html>")
            return _Response(_challenge())

        class Opener:
            def open(self, request, timeout=None):
                self.url = request.full_url
                raise _redirect_with_cookie(request.full_url)

        opener = Opener()
        with patch("nrw_events.http.urllib.request.urlopen", side_effect=urlopen), \
                patch("nrw_events.botgate.urllib.request.build_opener", return_value=opener):
            self.assertEqual(http.fetch_url(url), "<html>events</html>")
            # Same URL on the same day: served locally, no second request.
            self.assertEqual(http.fetch_url(url), "<html>events</html>")
        self.assertEqual(calls, [None, "botgate=abc123"])
        self.assertIn("/.well-known/botgate/verify?t=k1.test-token&n=", opener.url)

        # A new process reuses the persisted cookie without solving again.
        botgate.reset_for_tests()
        other = "https://www.bonn.de/other.php"
        with patch("nrw_events.http.urllib.request.urlopen", side_effect=urlopen), \
                patch("nrw_events.botgate.urllib.request.build_opener") as build_opener:
            self.assertEqual(http.fetch_url(other), "<html>events</html>")
        build_opener.assert_not_called()
        self.assertEqual(calls[-1], "botgate=abc123")

    def test_persistent_challenge_after_solve_fails_without_loop(self):
        class Opener:
            def open(self, request, timeout=None):
                raise _redirect_with_cookie(request.full_url)

        with patch("nrw_events.http.urllib.request.urlopen",
                   side_effect=lambda *_a, **_k: _Response(_challenge())) as urlopen, \
                patch("nrw_events.botgate.urllib.request.build_opener", return_value=Opener()):
            with self.assertRaises(botgate.BotgateError):
                http.fetch_url("https://www.bruehl.de/kalender.php")
            with self.assertRaises(botgate.BotgateError):
                http.fetch_url("https://www.bruehl.de/second.php")
        # First URL: challenge + retry. Second URL: one read, then refusal.
        self.assertEqual(urlopen.call_count, 3)
        self.assertIsNone(botgate.SESSION.cookie_header("bruehl.de"))

    def test_json_caller_learns_about_gate_behind_content_type_mismatch(self):
        def urlopen(request, timeout=None):
            if request.headers.get("Cookie") == "botgate=abc123":
                return _Response('[{"title": "A"}]', "application/json")
            return _Response(_challenge())

        class Opener:
            def open(self, request, timeout=None):
                raise _redirect_with_cookie(request.full_url)

        with patch("nrw_events.http.urllib.request.urlopen", side_effect=urlopen), \
                patch("nrw_events.botgate.urllib.request.build_opener", return_value=Opener()):
            self.assertEqual(http.fetch_json("https://www.bonn.de/citykey/events-json.php"), [{"title": "A"}])

    def test_ungated_hosts_are_neither_cached_nor_given_cookies(self):
        with patch("nrw_events.http.urllib.request.urlopen",
                   side_effect=lambda *_a, **_k: _Response("<html>ok</html>")) as urlopen:
            http.fetch_url("https://www.harmonie-bonn.de/")
            http.fetch_url("https://www.harmonie-bonn.de/")
        self.assertEqual(urlopen.call_count, 2)
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "gated-responses-v1")))

    def test_expired_response_cache_entry_is_refetched(self):
        with patch("nrw_events.http.urllib.request.urlopen",
                   side_effect=lambda *_a, **_k: _Response("<html>ok</html>")) as urlopen:
            http.fetch_url("https://www.huerth.de/a.php")
            later = time.time() + 21 * 60 * 60
            with patch.object(botgate.time, "time", return_value=later):
                http.fetch_url("https://www.huerth.de/a.php")
        self.assertEqual(urlopen.call_count, 2)

    def test_gated_hosts_are_spaced_one_second_apart(self):
        self.assertEqual(http._throttle_bucket("https://www.wesseling.de/a.php"), ("wesseling.de", 1.0))
        self.assertEqual(http._throttle_bucket("https://www.bonn.de/a.php")[0], "bonn.de")
        self.assertGreaterEqual(http._throttle_bucket("https://www.bonn.de/a.php")[1], 1.0)

    def test_gated_detail_namespaces_keep_pages_for_three_days(self):
        detail_cache._reset_detail_page_cache()
        with patch.dict(os.environ, {"NRW_EVENTS_GATED_RESPONSE_TTL_HOURS": "0"}), \
                patch("nrw_events.http.urllib.request.urlopen",
                      side_effect=lambda *_a, **_k: _Response("<html>detail</html>")) as urlopen:
            detail_cache.fetch_detail_url("https://www.bonn.de/d.php", cache_namespace="bonn-detail")
            later = time.time() + 48 * 60 * 60
            with patch.object(detail_cache.time, "time", return_value=later):
                detail_cache.fetch_detail_url("https://www.bonn.de/d.php", cache_namespace="bonn-detail")
        detail_cache._reset_detail_page_cache()
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
