import unittest
import urllib.error
from unittest import mock

from scripts import research_venue_geocoding as research


class VenueGeocodingResearchTests(unittest.TestCase):
    def test_legacy_error_entries_are_migrated_out_of_success_cache(self):
        cache = {"queries": {
            "retry me": {
                "fetchedAt": "2026-08-02T10:00:00+00:00",
                "error": "rate limited",
                "results": [],
            },
            "keep me": {"fetchedAt": "2026-08-02T10:00:00+00:00", "results": [{"id": 1}]},
        }}

        queries, errors = research.cache_buckets(cache)

        self.assertNotIn("retry me", queries)
        self.assertEqual(queries["keep me"]["results"], [{"id": 1}])
        self.assertEqual(errors["retry me"]["error"], "rate limited")

    def test_transient_fetch_retries_with_exponential_backoff(self):
        fetcher = mock.Mock(side_effect=[
            urllib.error.URLError("429"), TimeoutError("slow"), [{"id": 1}],
        ])

        with mock.patch.object(research.time, "sleep") as sleep:
            result = research.fetch_with_backoff(fetcher, "venue")

        self.assertEqual(result, [{"id": 1}])
        self.assertEqual(fetcher.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            [research.TRANSIENT_RETRY_BASE_SECONDS, research.TRANSIENT_RETRY_BASE_SECONDS * 2],
        )

    def test_exhausted_transient_fetch_is_not_converted_to_empty_results(self):
        fetcher = mock.Mock(side_effect=urllib.error.URLError("still unavailable"))

        with mock.patch.object(research.time, "sleep"), self.assertRaises(urllib.error.URLError):
            research.fetch_with_backoff(fetcher, "venue")

        self.assertEqual(fetcher.call_count, research.TRANSIENT_RETRY_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
