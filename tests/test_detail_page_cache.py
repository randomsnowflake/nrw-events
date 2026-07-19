import os
import tempfile
import unittest
from unittest.mock import patch

from nrw_events import common


class DetailPageCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "NRW_EVENTS_CACHE_DIR": self.cache_dir.name,
                "NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "24",
            },
        )
        self.env.start()
        common._reset_detail_page_cache()

    def tearDown(self):
        common._reset_detail_page_cache()
        self.env.stop()
        self.cache_dir.cleanup()

    def test_successful_detail_response_survives_a_new_process_cache(self):
        url = "https://example.org/events/detail/42"
        with patch.object(common, "fetch_url", return_value="<main>Event detail</main>") as fetch:
            self.assertEqual(
                common.fetch_detail_url(url, cache_namespace="example"),
                "<main>Event detail</main>",
            )
        fetch.assert_called_once_with(url, timeout=15)

        common._reset_detail_page_cache()
        with patch.object(common, "fetch_url", side_effect=AssertionError("cache miss")) as fetch:
            self.assertEqual(
                common.fetch_detail_url(url, cache_namespace="example"),
                "<main>Event detail</main>",
            )
        fetch.assert_not_called()

    def test_zero_ttl_disables_memory_and_disk_caching(self):
        url = "https://example.org/events/detail/uncached"
        with patch.dict(os.environ, {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"}), \
                patch.object(common, "fetch_url", side_effect=["first", "second"]) as fetch:
            self.assertEqual(common.fetch_detail_url(url, cache_namespace="example"), "first")
            self.assertEqual(common.fetch_detail_url(url, cache_namespace="example"), "second")

        self.assertEqual(fetch.call_count, 2)

    def test_failed_detail_request_is_not_cached(self):
        url = "https://example.org/events/detail/retry"
        with patch.object(
            common,
            "fetch_url",
            side_effect=[TimeoutError("temporary"), "recovered"],
        ) as fetch:
            with self.assertRaises(TimeoutError):
                common.fetch_detail_url(url, cache_namespace="example")
            self.assertEqual(
                common.fetch_detail_url(url, cache_namespace="example"),
                "recovered",
            )

        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
