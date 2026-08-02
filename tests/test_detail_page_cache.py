import json
import os
import tempfile
import time
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
        fetch.assert_called_once_with(url, timeout=15, cache=False)

        common._reset_detail_page_cache()
        with patch.object(common, "fetch_url", side_effect=AssertionError("cache miss")) as fetch:
            self.assertEqual(
                common.fetch_detail_url(url, cache_namespace="example"),
                "<main>Event detail</main>",
            )
        fetch.assert_not_called()

    def test_persistent_detail_cache_is_private_and_does_not_store_raw_url_keys(self):
        url = "https://example.org/detail?token=secret-value"
        with patch.object(common, "fetch_url", return_value="<main>Event detail</main>"):
            common.fetch_detail_url(url, cache_namespace="private")
        common.flush_detail_page_caches("private")

        cache_files = [
            path for path in os.scandir(self.cache_dir.name)
            if path.name.startswith("detail-pages-") and path.name.endswith(".json")
        ]
        self.assertEqual(len(cache_files), 1)
        cache_path = cache_files[0].path
        self.assertEqual(os.stat(self.cache_dir.name).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(cache_path).st_mode & 0o777, 0o600)
        with open(cache_path, encoding="utf-8") as handle:
            persisted = handle.read()
        self.assertNotIn(url, persisted)
        self.assertNotIn("secret-value", persisted)

    def test_flush_keeps_newer_entry_written_by_another_process(self):
        url = "https://example.org/detail/concurrent"
        with patch.object(common, "fetch_url", return_value="older body"):
            common.fetch_detail_url(url, cache_namespace="concurrent")

        cache_path = common._detail_page_cache_path("concurrent")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_key = common._detail_page_cache_key(url)
        cache_path.write_text(
            json.dumps({
                "version": common._DETAIL_PAGE_CACHE_VERSION,
                "namespace": "concurrent",
                "entries": {
                    cache_key: {"fetched_at": time.time() + 1, "body": "newer body"},
                },
            }),
            encoding="utf-8",
        )

        common.flush_detail_page_caches("concurrent")
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["entries"][cache_key]["body"], "newer body")

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
