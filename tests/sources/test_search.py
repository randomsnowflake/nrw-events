import unittest
from datetime import datetime
from unittest import mock

from nrw_events.health import SourceStatus
from nrw_events.sources import search

from tests.helpers import patch_window


class SearchSourceTests(unittest.TestCase):
    def test_exa_without_key_is_disabled_without_request(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(search.common, "log_source_disabled") as disabled, \
                mock.patch.object(search.common, "post_json") as post_json:
            self.assertEqual(search.fetch_exa(), [])

        post_json.assert_not_called()
        disabled.assert_called_once_with(
            "Exa Search", "disabled: EXA_API_KEY is not configured",
        )

    def test_exa_parses_results_and_isolates_request_errors(self):
        response = {
            "results": [{
                "title": "Sommerfest Bonn", "url": "https://example.test/fest",
                "publishedDate": "2026-08-03", "text": "Am 15. August in Bonn",
            }],
        }
        parsed = {"title": "Sommerfest Bonn", "source": "Exa Search"}
        with mock.patch.dict("os.environ", {"EXA_API_KEY": "secret"}, clear=True), \
                mock.patch.object(search, "search_queries", return_value=["query one", "query two"]), \
                mock.patch.object(search.common, "post_json", side_effect=[response, RuntimeError("down")]) as post_json, \
                mock.patch.object(search.common, "search_result_event", return_value=parsed) as parse_result, \
                mock.patch.object(search.common, "log_source_error") as log_error:
            events = search.fetch_exa()

        self.assertEqual(events, [parsed])
        self.assertEqual(post_json.call_count, 2)
        self.assertEqual(post_json.call_args_list[0].args[0], "https://api.exa.ai/search")
        self.assertEqual(post_json.call_args_list[0].kwargs["headers"], {"x-api-key": "secret"})
        parse_result.assert_called_once_with(
            "Sommerfest Bonn", "https://example.test/fest",
            "Am 15. August in Bonn", "Exa Search", 0.58,
        )
        log_error.assert_called_once()

    def test_exa_publish_date_never_becomes_the_event_date(self):
        patch_window(self, datetime(2026, 9, 3), datetime(2026, 10, 31))
        response = {
            "results": [{
                "title": "Weinfest Bonn", "url": "https://example.test/fest",
                "publishedDate": "2026-09-03",
                "text": "Das Herbstfest findet am 10.10.2026 in Bonn statt.",
            }],
        }
        with mock.patch.dict("os.environ", {"EXA_API_KEY": "secret"}, clear=True), \
                mock.patch.object(search, "search_queries", return_value=["query"]), \
                mock.patch.object(search.common, "post_json", return_value=response):
            [event] = search.fetch_exa()

        self.assertEqual(event["date"], "2026-10-10")

    def test_grok_is_retired_even_with_legacy_key_and_opt_in(self):
        for environment in ({}, {"XAI_API_KEY": "secret"},
                            {"XAI_API_KEY": "secret", "NRW_EVENTS_ENABLE_GROK": "yes"}):
            with self.subTest(environment_keys=sorted(environment)), \
                    mock.patch.dict("os.environ", environment, clear=True), \
                    mock.patch.object(search.common, "post_json") as post_json:
                result = search.fetch_grok()
                self.assertEqual(result.events, ())
                self.assertEqual(result.status, SourceStatus.DISABLED)
                self.assertEqual(result.disabled_reason, "Grok event search permanently retired")
                post_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
