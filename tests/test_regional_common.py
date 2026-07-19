import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.nrw_events import common
from scripts.nrw_events.sources import regional_common


class RegionalCommonHealthTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 7, 19)
        common.END_DATE = datetime(2026, 8, 1)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

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
                "Seasonal calendar", "https://example.test/events", parser)

        self.assertEqual(events, [])
        log_source_error.assert_not_called()
        record_endpoint.assert_called_once_with(
            "https://example.test/events",
            parser_type="html",
            candidate_count=1,
            out_of_window_count=1,
            parsed_event_count=0,
            parser_empty=False,
        )

    def test_no_parser_candidates_still_reports_layout_drift(self):
        with patch.object(common, "fetch_url", return_value="<html>changed layout</html>"), \
             patch.object(common, "_record_endpoint") as record_endpoint, \
             patch.object(common, "log_source_error") as log_source_error:
            events = regional_common.fetch_html_events(
                "Broken calendar", "https://example.test/events", lambda _html: [])

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


if __name__ == "__main__":
    unittest.main()
