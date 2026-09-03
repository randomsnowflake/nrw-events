import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import regional_common

from tests.helpers import patch_window


class RegionalCommonHealthTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 19), datetime(2026, 8, 1))

    def test_out_of_window_candidates_are_healthy_empty(self):
        def parser(_html):
            event = common.make_event(
                "Autumn concert",
                datetime(2026, 9, 1),
                None,
                "Town hall",
                "Bonn",
                "",
                "https://example.test/autumn-concert",
                "Seasonal calendar",
                "concert",
            )
            return [event] if event else []

        with patch.object(common, "fetch_url", return_value="<html></html>"), \
             patch.object(common, "_record_endpoint") as record_endpoint, \
             patch.object(common, "log_source_error") as log_source_error:
            events = regional_common.fetch_html_events(
                "Seasonal calendar", "https://example.test/events", parser,
                source_id="seasonal-calendar")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_id"], "seasonal-calendar")
        log_source_error.assert_not_called()
        record_endpoint.assert_called_once_with(
            "https://example.test/events",
            parser_type="html",
            candidate_count=1,
            out_of_window_count=1,
            parsed_event_count=1,
            parser_empty=False,
        )

    def test_dedupe_keeps_distinct_same_day_showtimes(self):
        base = {
            "source": "Cinema", "title": "Casablanca", "date": "2026-07-20",
            "city": "Bonn", "venue": "Filmhaus",
        }
        events = [
            {**base, "time": "18:00", "start_at": "2026-07-20T18:00+02:00"},
            {**base, "time": "21:00", "start_at": "2026-07-20T21:00+02:00"},
        ]

        self.assertEqual(regional_common.dedupe(events), events)

    def test_time_text_prefers_beginn_and_accepts_german_clock_forms(self):
        self.assertEqual(
            regional_common.time_text("Einlass 18:00 Uhr, Beginn 19:00 Uhr"),
            "19:00",
        )
        self.assertEqual(regional_common.time_text("Beginn 19.30 Uhr"), "19:30")
        self.assertEqual(regional_common.time_text("ab 10 Uhr"), "10:00")

    def test_out_of_window_events_skip_detail_enrichment(self):
        event = common.make_event(
            "Autumn concert",
            datetime(2026, 9, 1),
            None,
            "Town hall",
            "Bonn",
            "",
            "https://example.test/autumn-concert",
            "Seasonal calendar",
            "concert",
        )
        detail_fetcher = patch.object(common, "fetch_detail_url")
        fetch_detail = detail_fetcher.start()
        self.addCleanup(detail_fetcher.stop)

        enriched = regional_common.enrich_descriptions(
            [event],
            source="Seasonal calendar",
            cache_namespace="seasonal",
            extract_context=lambda html, _event: {"description": html},
            fallback=lambda _event: "fallback",
        )

        self.assertEqual(enriched, [event])
        fetch_detail.assert_not_called()

    def test_expected_empty_predicate_only_suppresses_matching_pages(self):
        marker = "No events are currently available."

        for html, expected_parser_empty in (
            (f"<div>{marker}</div>", False),
            ("<html>changed layout</html>", True),
        ):
            with self.subTest(html=html), \
                 patch.object(common, "fetch_url", return_value=html), \
                 patch.object(common, "_record_endpoint") as record_endpoint, \
                 patch.object(common, "log_source_error") as log_source_error:
                events = regional_common.fetch_html_events(
                    "Seasonal calendar",
                    "https://example.test/events",
                    lambda _html: [],
                    source_id="seasonal-calendar",
                    empty_is_healthy=lambda body: marker in body,
                )

            self.assertEqual(events, [])
            self.assertEqual(
                record_endpoint.call_args.kwargs["parser_empty"],
                expected_parser_empty,
            )
            if expected_parser_empty:
                self.assertIsInstance(
                    log_source_error.call_args.args[1],
                    regional_common.ParserEmptyError,
                )
            else:
                log_source_error.assert_not_called()

    def test_no_parser_candidates_still_reports_layout_drift(self):
        with patch.object(common, "fetch_url", return_value="<html>changed layout</html>"), \
             patch.object(common, "_record_endpoint") as record_endpoint, \
             patch.object(common, "log_source_error") as log_source_error:
            events = regional_common.fetch_html_events(
                "Broken calendar", "https://example.test/events", lambda _html: [],
                source_id="broken-calendar")

        self.assertEqual(events, [])
        record_endpoint.assert_called_once_with(
            "https://example.test/events",
            parser_type="html",
            candidate_count=0,
            out_of_window_count=0,
            parsed_event_count=0,
            parser_empty=True,
        )
        error = log_source_error.call_args.args[1]
        self.assertIsInstance(error, regional_common.ParserEmptyError)
        self.assertEqual(str(error), "parser returned no event records")

    def test_class_tag_helpers_ignore_attribute_order_and_prefixed_names(self):
        html = (
            '<article data-class="wrong" class="SP-Teaser">'
            '<a data-href="/wrong" href="/right" class="other SP-Teaser__inner">Event</a>'
            '</article>'
        )

        blocks = regional_common.class_tag_blocks(html, "article", "SP-Teaser")

        self.assertEqual(len(blocks), 1)
        self.assertEqual(
            regional_common.attribute_from_class_tag(
                blocks[0], "a", "SP-Teaser__inner", "href"
            ),
            "/right",
        )


if __name__ == "__main__":
    unittest.main()
