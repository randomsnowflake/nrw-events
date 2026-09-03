"""Separating HTTP and parsing must preserve the complete raw-event contract."""

import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from nrw_events import common, core, performance
from nrw_events.runtime import EventWindow

from .helpers import make_runner_env


class ICalParseBoundaryTests(unittest.TestCase):
    def test_direct_parser_and_fetch_wrapper_return_identical_records(self):
        raw = (Path(__file__).parent / "data" / "performance" / "events.ics").read_text()
        with make_runner_env() as environment:
            context = replace(environment.context(), window=EventWindow(datetime(2026, 9, 3), datetime(2026, 12, 1)))
            old_window = (common.DAYS_AHEAD, common.TODAY, common.END_DATE)
            token = common.configure_context(context)
            try:
                collector = performance.Collector()
                with performance.collect(collector):
                    fetched = core.fetch_ical("https://example.test/feed", "Fixture", "Bonn", fetcher=lambda *args, **kwargs: raw)
                    parsed = core.parse_ical(raw, "https://example.test/feed", "Fixture", "Bonn")
                self.assertEqual(fetched, parsed)
                self.assertGreater(len(parsed), 5)
                self.assertEqual(collector.snapshot()["stages"]["ical.parse_canonicalize"]["calls"], 2)
            finally:
                common.reset_runtime(token)
                common.DAYS_AHEAD, common.TODAY, common.END_DATE = old_window
                common._configure_date_reference(old_window[1])


if __name__ == "__main__":
    unittest.main()
