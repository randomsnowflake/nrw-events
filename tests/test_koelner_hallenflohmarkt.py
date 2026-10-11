import unittest
from datetime import datetime
from pathlib import Path

from nrw_events.market_source_fallbacks import partition_directory_fallbacks
from nrw_events.sources import SOURCES, koelner_hallenflohmarkt, regional_common
from nrw_events.validation import validate_event

from tests.helpers import patch_window

_FIXTURE = Path(__file__).parent / "fixtures" / "koelner_hallenflohmarkt_20261011.html"


class KoelnerHallenflohmarktTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 11), datetime(2026, 11, 30))
        self.html = _FIXTURE.read_text(encoding="utf-8")

    def test_source_is_registered(self):
        self.assertIs(SOURCES["Kölner Hallenflohmarkt"], koelner_hallenflohmarkt.fetch)

    def test_live_page_dates_take_the_year_from_the_booking_form(self):
        events = koelner_hallenflohmarkt._events_from_page(self.html, strict=True)

        self.assertEqual(
            [event["date"] for event in events],
            ["2026-10-11", "2026-10-18", "2026-10-25", "2026-11-01"],
        )
        event = events[0]
        self.assertEqual(event["time"], "10:00–16:00")
        self.assertEqual(event["city"], "Köln")
        self.assertEqual(event["venue_address"], "Industriestraße 131")
        self.assertEqual(event["price"], "kostenlos")
        self.assertEqual(event["source_id"], "koelner-hallenflohmarkt")
        self.assertEqual(event["organizer"], "Kölner Hallenflohmarkt")
        self.assertTrue(event["description"].startswith("Wir laden Sie ein"))

    def test_listed_date_without_booking_year_is_skipped(self):
        html = self.html.replace('data-hook="checkbox-18.10.26"', "")

        dates = [e["date"] for e in koelner_hallenflohmarkt._events_from_page(html, strict=True)]

        self.assertEqual(dates, ["2026-10-11", "2026-10-25", "2026-11-01"])

    def test_booking_year_that_is_not_a_sunday_is_rejected(self):
        html = self.html.replace("checkbox-11.10.26", "checkbox-11.10.27")

        dates = [e["date"] for e in koelner_hallenflohmarkt._events_from_page(html, strict=True)]

        self.assertNotIn("2027-10-11", dates)
        self.assertNotIn("2026-10-11", dates)

    def test_changed_page_contract_is_parser_empty(self):
        with self.assertRaisesRegex(regional_common.ParserEmptyError, "address"):
            koelner_hallenflohmarkt._events_from_page(
                self.html.replace("Industriestraße 131", "Irgendwo 1"), strict=True,
            )
        with self.assertRaisesRegex(regional_common.ParserEmptyError, "date list"):
            koelner_hallenflohmarkt._events_from_page(
                self.html.replace("Kommende Termine", "Termine"), strict=True,
            )

    def test_primary_replaces_only_matching_marktcom_occurrences(self):
        def directory(date):
            return validate_event({
                "title": "Kölner Hallenflohmarkt", "source": "marktcom",
                "source_id": "marktcom", "date": date, "start_date": date,
                "end_date": date, "city": "Köln", "venue": "Kölner Hallenflohmarkt",
                "organizer": "Kölner Hallenflohmarkt", "score": 1.0,
            })

        primary = [
            validate_event(event)
            for event in koelner_hallenflohmarkt._events_from_page(self.html, strict=True)
            if event["date"] == "2026-10-18"
        ]
        cohort = [directory("2026-10-18"), directory("2026-11-08")]

        kept, replaced = partition_directory_fallbacks([*cohort, *primary])

        self.assertEqual([e.start_date for e in replaced], ["2026-10-18"])
        self.assertEqual(
            sorted((e.start_date, e.source_id) for e in kept),
            [("2026-10-18", "koelner-hallenflohmarkt"), ("2026-11-08", "marktcom")],
        )


if __name__ == "__main__":
    unittest.main()
