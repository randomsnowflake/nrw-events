import os
import tempfile
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from nrw_events import common


class _Response:
    status = 200

    def __init__(self, body: bytes, content_type: str = "text/html"):
        self._body = body
        self.headers = Message()
        self.headers["Content-Type"] = f"{content_type}; charset=utf-8"

    def read(self, _limit: int) -> bytes:
        return self._body

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class HttpCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "NRW_EVENTS_CACHE_DIR": self.cache_dir.name,
                "NRW_EVENTS_HTTP_CACHE_TTL_HOURS": "26",
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "72",
            },
        )
        self.env.start()
        common._reset_detail_page_cache()

    def tearDown(self):
        common._reset_detail_page_cache()
        self.env.stop()
        self.cache_dir.cleanup()

    def test_default_cache_durations_cover_daily_fetches_and_multi_day_details(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NRW_EVENTS_HTTP_CACHE_TTL_HOURS", None)
            os.environ.pop("NRW_EVENTS_DETAIL_CACHE_TTL_HOURS", None)
            self.assertGreaterEqual(common._http_cache_ttl_seconds(), 24 * 60 * 60)
            self.assertGreaterEqual(common._detail_page_cache_ttl_seconds(), 72 * 60 * 60)

    def test_successful_get_survives_a_new_process_cache(self):
        url = "https://example.org/calendar.json"
        with patch.object(common.urllib.request, "urlopen", return_value=_Response(b'{"ok":true}', "application/json")) as request:
            self.assertEqual(common.fetch_url(url, accept="application/json"), '{"ok":true}')
        request.assert_called_once()
        common.flush_detail_page_caches()

        common._reset_detail_page_cache()
        with patch.object(common.urllib.request, "urlopen", side_effect=AssertionError("cache miss")) as request:
            self.assertEqual(common.fetch_url(url, accept="application/json"), '{"ok":true}')
        request.assert_not_called()

    def test_request_headers_are_part_of_the_cache_identity_without_being_persisted(self):
        url = "https://example.org/private-feed"
        with patch.object(
            common.urllib.request,
            "urlopen",
            side_effect=[_Response(b"alpha"), _Response(b"beta")],
        ) as request:
            self.assertEqual(common.fetch_url(url, headers={"Authorization": "Bearer alpha"}), "alpha")
            self.assertEqual(common.fetch_url(url, headers={"Authorization": "Bearer beta"}), "beta")
        self.assertEqual(request.call_count, 2)
        common.flush_detail_page_caches()

        cache_text = next(iter(__import__("pathlib").Path(self.cache_dir.name).glob("http-get-*.json"))).read_text()
        self.assertNotIn("Bearer alpha", cache_text)
        self.assertNotIn("Bearer beta", cache_text)
        self.assertNotIn(url, cache_text)

    def test_zero_ttl_disables_get_cache(self):
        url = "https://example.org/live"
        with patch.dict(os.environ, {"NRW_EVENTS_HTTP_CACHE_TTL_HOURS": "0"}), patch.object(
            common.urllib.request,
            "urlopen",
            side_effect=[_Response(b"first"), _Response(b"second")],
        ) as request:
            self.assertEqual(common.fetch_url(url), "first")
            self.assertEqual(common.fetch_url(url), "second")
        self.assertEqual(request.call_count, 2)

    def test_idempotent_post_json_uses_payload_aware_daily_cache(self):
        url = "https://api.example.org/search"
        with patch.object(
            common.urllib.request,
            "urlopen",
            side_effect=[_Response(b'{"result":"a"}', "application/json"),
                         _Response(b'{"result":"b"}', "application/json")],
        ) as request:
            self.assertEqual(common.post_json(url, {"query": "a"}, retry_safe=True), {"result": "a"})
            self.assertEqual(common.post_json(url, {"query": "a"}, retry_safe=True), {"result": "a"})
            self.assertEqual(common.post_json(url, {"query": "b"}, retry_safe=True), {"result": "b"})
        self.assertEqual(request.call_count, 2)

    def test_non_idempotent_post_json_is_never_cached(self):
        url = "https://api.example.org/generate"
        with patch.object(
            common.urllib.request,
            "urlopen",
            side_effect=[_Response(b'{"result":"a"}', "application/json"),
                         _Response(b'{"result":"b"}', "application/json")],
        ) as request:
            self.assertEqual(common.post_json(url, {"prompt": "x"}), {"result": "a"})
            self.assertEqual(common.post_json(url, {"prompt": "x"}), {"result": "b"})
        self.assertEqual(request.call_count, 2)

    def test_corrupt_cached_post_response_is_discarded_and_refetched(self):
        url = "https://api.example.org/search"
        payload = {"query": "Bonn"}
        cache_key = common._post_cache_key(url, payload, None, "json")
        common._http_cache_store(cache_key, "not-json")
        with patch.object(
            common.urllib.request,
            "urlopen",
            return_value=_Response(b'{"events":[1]}'),
        ) as request:
            self.assertEqual(
                common.post_json(url, payload, retry_safe=True),
                {"events": [1]},
            )
        request.assert_called_once()

    def test_brightdata_fallback_populates_the_daily_direct_cache(self):
        url = "https://events.example.org/calendar"
        rate_limited = urllib.error.HTTPError(url, 429, "rate limited", Message(), None)
        self.addCleanup(rate_limited.close)
        bright_response = _Response(
            b'{"status_code":200,"body":"<main>calendar</main>"}',
            "application/json",
        )
        old_attempts = common._HTTP_RETRY_ATTEMPTS
        common._HTTP_RETRY_ATTEMPTS = 1
        try:
            with patch.dict(os.environ, {
                "BRIGHT_DATA_API_KEY": "secret",
                "BRIGHT_DATA_ZONE": "events",
            }), patch.object(
                common.urllib.request,
                "urlopen",
                side_effect=[rate_limited, bright_response],
            ) as request:
                self.assertEqual(
                    common.fetch_url_with_brightdata_fallback(
                        url,
                        allowed_hosts=("events.example.org",),
                        fallback_statuses=(429,),
                    ),
                    "<main>calendar</main>",
                )
            self.assertEqual(request.call_count, 2)

            with patch.object(
                common.urllib.request, "urlopen", side_effect=AssertionError("cache miss")
            ) as request:
                self.assertEqual(
                    common.fetch_url_with_brightdata_fallback(
                        url,
                        allowed_hosts=("events.example.org",),
                        fallback_statuses=(429,),
                    ),
                    "<main>calendar</main>",
                )
            request.assert_not_called()
        finally:
            common._HTTP_RETRY_ATTEMPTS = old_attempts


if __name__ == "__main__":
    unittest.main()
