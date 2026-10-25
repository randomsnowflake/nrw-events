"""Rheinauen-Flohmarkt from the organizer page; fixtures are original melan.de excerpts.

melan-bonn-rheinaue-20260520-season.html: web.archive.org snapshot 20260520135245.
melan-bonn-rheinaue-20261025-no-dates.html: live page fetched 2026-10-25.
"""
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from nrw_events import common
from nrw_events.sources import flohmarkt, regional_common

from tests.helpers import patch_window

FIXTURES = Path(__file__).parent / "fixtures"
SEASON = (FIXTURES / "melan-bonn-rheinaue-20260520-season.html").read_text()
NO_DATES = (FIXTURES / "melan-bonn-rheinaue-20261025-no-dates.html").read_text()


class RheinauenFlohmarktSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 5, 20), datetime(2026, 12, 31))

    def test_reads_every_published_saturday_from_the_organizer_page(self):
        events = flohmarkt._events_from_page(SEASON, strict=True)

        self.assertEqual(
            [event["date"] for event in events],
            ["2026-06-20", "2026-07-18", "2026-08-15", "2026-09-19", "2026-10-17"],
        )
        self.assertTrue(all(event["time"] == "08:00–18:00" for event in events))
        self.assertEqual({event["title"] for event in events}, {"Rheinauen-Flohmarkt"})
        self.assertEqual({event["source_id"] for event in events}, {"rheinauen-flohmarkt"})
        self.assertEqual(events[0]["link"], flohmarkt._URL)

    def test_no_published_dates_is_a_healthy_empty_and_never_extrapolates(self):
        patch_window(self, datetime(2026, 10, 25), datetime(2027, 12, 31))
        with mock.patch.object(common, "fetch_url", return_value=NO_DATES), \
             mock.patch.object(common, "_record_endpoint") as record, \
             mock.patch.object(common, "log_source_error") as warning:
            self.assertEqual(flohmarkt.fetch(), [])
        self.assertFalse(record.call_args.kwargs["parser_empty"])
        self.assertFalse(warning.called)

    def test_contract_breaks_stay_visible(self):
        broken_pages = [
            "<html>Not Found</html>",
            NO_DATES.replace("missing-market-dates-notice", "notice"),
            SEASON.replace("Bonn RHEINAUE", "Aachen PORTA"),
            SEASON.replace("Ludwig-Erhard-Allee 20", "Einsteinstraße 28"),
            SEASON.replace("<h3>Öffnungszeit</h3>", "<h3>Zeiten</h3>"),
            SEASON.replace("<span>Sa, 20.06.2026</span>", "<span>Sa, 31.06.2026</span>"),
        ]
        for html in broken_pages:
            with self.subTest(html=html[:60]):
                with self.assertRaises(regional_common.ParserEmptyError):
                    flohmarkt._events_from_page(html, strict=True)
                self.assertFalse(flohmarkt._SPEC.empty_is_healthy(html))


if __name__ == "__main__":
    unittest.main()
