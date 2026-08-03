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

    def test_persist_drops_expired_entries_merged_from_disk(self):
        namespace = "example"
        path = common._detail_page_cache_path(namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        path.write_text(json.dumps({
            "version": common._DETAIL_PAGE_CACHE_VERSION,
            "namespace": namespace,
            "entries": {
                "https://example.org/fresh": {"fetched_at": now, "body": "fresh"},
                "https://example.org/expired": {"fetched_at": now - 25 * 60 * 60, "body": "expired"},
            },
        }), encoding="utf-8")

        common._load_detail_page_cache(namespace, 24 * 60 * 60)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"]["https://example.org/expired"] = {
            "fetched_at": now - 25 * 60 * 60, "body": "expired",
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        with patch.object(common, "fetch_url", return_value="new"):
            common.fetch_detail_url("https://example.org/new", cache_namespace=namespace)
        common.flush_detail_page_caches(namespace)

        persisted = json.loads(path.read_text(encoding="utf-8"))["entries"]
        self.assertEqual(set(persisted), {"https://example.org/fresh", "https://example.org/new"})

    def test_persist_enforces_entry_and_byte_caps_with_lru_eviction(self):
        namespace = "bounded"
        urls = [f"https://example.org/{name}" for name in ("first", "second", "third")]
        with patch.dict(os.environ, {
                "NRW_EVENTS_DETAIL_CACHE_MAX_ENTRIES": "2",
                "NRW_EVENTS_DETAIL_CACHE_MAX_BYTES": "600",
            }), patch.object(common, "fetch_url", side_effect=["a" * 80, "b" * 80, "c" * 80]):
            common.fetch_detail_url(urls[0], cache_namespace=namespace)
            common.fetch_detail_url(urls[1], cache_namespace=namespace)
            common.fetch_detail_url(urls[0], cache_namespace=namespace)
            common.fetch_detail_url(urls[2], cache_namespace=namespace)
            common.flush_detail_page_caches(namespace)

        path = common._detail_page_cache_path(namespace)
        persisted = json.loads(path.read_text(encoding="utf-8"))["entries"]
        self.assertEqual(set(persisted), {urls[0], urls[2]})
        self.assertLessEqual(path.stat().st_size, 600)

    def test_pruning_skips_oversized_newest_entry_before_counting_limit(self):
        now = time.time()
        entries = {
            "https://example.org/oversized": {
                "fetched_at": now,
                "accessed_at": now,
                "body": "x" * 1_000,
            },
            "https://example.org/fits": {
                "fetched_at": now - 1,
                "accessed_at": now - 1,
                "body": "ok",
            },
        }
        with patch.dict(os.environ, {
                "NRW_EVENTS_DETAIL_CACHE_MAX_ENTRIES": "1",
                "NRW_EVENTS_DETAIL_CACHE_MAX_BYTES": "250",
            }):
            retained = common._prune_detail_page_cache_entries(
                entries, namespace="bounded", ttl_seconds=60, now=now,
            )

        self.assertEqual(set(retained), {"https://example.org/fits"})

    def test_cache_key_separates_transport_and_request_parameters(self):
        url = "https://example.org/events/detail/parameters"
        with patch.object(
            common,
            "fetch_url",
            side_effect=["html", "calendar", "long-timeout"],
        ) as direct, patch.object(
            common,
            "fetch_url_with_brightdata",
            return_value="proxy",
        ) as proxy:
            self.assertEqual(
                common.fetch_detail_url(
                    url, cache_namespace="parameters", accept="text/html"
                ),
                "html",
            )
            self.assertEqual(
                common.fetch_detail_url(
                    url, cache_namespace="parameters", accept="text/calendar"
                ),
                "calendar",
            )
            self.assertEqual(
                common.fetch_detail_url(
                    url, cache_namespace="parameters", accept="text/html", timeout=30
                ),
                "long-timeout",
            )
            self.assertEqual(
                common.fetch_detail_url(
                    url, cache_namespace="parameters", accept="text/html", brightdata=True
                ),
                "proxy",
            )
            self.assertEqual(
                common.fetch_detail_url(
                    url, cache_namespace="parameters", accept="text/html"
                ),
                "html",
            )

        self.assertEqual(direct.call_count, 3)
        proxy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
