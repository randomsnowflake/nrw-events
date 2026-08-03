import unittest
from unittest import mock

from nrw_events.sources import search


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
            "2026-08-03 Am 15. August in Bonn", "Exa Search", 0.58,
        )
        log_error.assert_called_once()

    def test_grok_requires_key_and_explicit_opt_in(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(search.common, "log_source_disabled") as disabled:
            self.assertEqual(search.fetch_grok(), [])
            disabled.assert_called_once_with(
                "Grok Search", "disabled: XAI_API_KEY is not configured",
            )

        with mock.patch.dict("os.environ", {"XAI_API_KEY": "secret"}, clear=True), \
                mock.patch.object(search.common, "log_source_disabled") as disabled, \
                mock.patch.object(search.common, "post_json") as post_json:
            self.assertEqual(search.fetch_grok(), [])
            post_json.assert_not_called()
            disabled.assert_called_once_with(
                "Grok Search", "disabled: set NRW_EVENTS_ENABLE_GROK=1 to enable Grok search",
            )

    def test_grok_extracts_assistant_json_and_normalizes_fields(self):
        response = {
            "output": [
                {"type": "reasoning", "content": []},
                {
                    "type": "message", "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": '[{"name":"Weinwanderung","date":"2026-08-22",'
                                '"city":"bad neuenahr","venue":"Kurpark",'
                                '"description":"Geführte Tour","link":"https://example.test/wine"}]',
                    }],
                },
            ],
        }
        parsed = {"title": "Weinwanderung", "city": "Bonn", "venue": ""}
        environment = {"XAI_API_KEY": "secret", "NRW_EVENTS_ENABLE_GROK": "yes"}
        with mock.patch.dict("os.environ", environment, clear=True), \
                mock.patch.object(search, "search_queries", return_value=["Ahrtal events"]), \
                mock.patch.object(search.common, "post_json", return_value=response) as post_json, \
                mock.patch.object(search.common, "search_result_event", return_value=parsed) as parse_result:
            events = search.fetch_grok()

        self.assertEqual(events, [{
            "title": "Weinwanderung", "date": "2026-08-22",
            "city": "Bad Neuenahr", "venue": "Kurpark",
        }])
        self.assertEqual(post_json.call_args.args[0], "https://api.x.ai/v1/responses")
        self.assertEqual(post_json.call_args.kwargs["headers"], {"Authorization": "Bearer secret"})
        parse_result.assert_called_once_with(
            "Weinwanderung", "https://example.test/wine",
            "2026-08-22 Kurpark Geführte Tour", "Grok Search", 0.7,
        )


if __name__ == "__main__":
    unittest.main()
