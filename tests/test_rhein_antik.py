"""Contract tests for the Rhein Antik organizer schedule.

The fixture mirrors the real Elementor markup: marketing badges are appended
*inside* the location item, and only the heading carries the year.
"""

import unittest
from datetime import datetime

from nrw_events import report
from nrw_events.sources import SOURCES, rhein_antik

from tests.helpers import patch_window


def _item(text):
    return (
        '<ul class="elementor-icon-list-items"><li class="elementor-icon-list-item">'
        '<span class="elementor-icon-list-icon"><svg></svg></span>'
        f'<span class="elementor-icon-list-text">{text}</span>'
        "</li></ul>"
    )


FIXTURE = (
    '<h2 class="elementor-heading-title">'
    "Geplante Antik-, Kunst- &amp; Design-Märkte 2026</h2>"
    + _item("So 12. April")
    + _item("Bonn - Friedensplatz")
    + _item("Mi 3. bis So 7. Juni")
    + _item("Aachen / Kornelimünster - hist. Jahrmarkt WIEDER DA!!!")
    + _item("Sa 18. &amp; So 19. Juli")
    + _item("Königswinter - Marktplatz")
    + _item("So 16. August")
    + _item("Bonn - Friedensplatz")
    + _item("So 27. September")
    + _item("Siegburg - Marktplatz NEUER TERMIN")
    + _item("Sa 10. &amp; So 11. Okt.")
    + _item("Bendorf - Industriedenkmal Sayner Hütte NEU!!!")
    + _item("So 25. Oktober")
    + _item("Bad Schwalbach - Kurhaus INDOOR NEU!!!")
    + _item("So 13. Dezember")
    + _item("Koblenz / Mülheim-Kärlich CORE Eventlocation INDOOR NEU!!!")
)


class RheinAntikSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 1, 1), datetime(2026, 12, 31))

    def _events(self):
        return rhein_antik.events_from_listing(FIXTURE)

    def _by_date(self, start_date):
        return next((e for e in self._events() if e["start_date"] == start_date), None)

    def test_source_is_registered(self):
        self.assertIs(SOURCES["Rhein Antik"], rhein_antik.fetch)

    def test_single_date_market_uses_heading_year_and_splits_venue(self):
        event = self._by_date("2026-04-12")

        self.assertIsNotNone(event)
        self.assertEqual(event["city"], "Bonn")
        self.assertEqual(event["venue"], "Friedensplatz")
        self.assertEqual(event["title"], "Antik-, Kunst- & Designmarkt Bonn")

    def test_bis_range_and_ampersand_pair_both_produce_end_dates(self):
        weekend = self._by_date("2026-07-18")
        self.assertIsNotNone(weekend)
        self.assertEqual(weekend["end_date"], "2026-07-19")

        multi_day = self._by_date("2026-06-03")
        self.assertIsNotNone(multi_day)
        self.assertEqual(multi_day["end_date"], "2026-06-07")

    def test_month_abbreviation_with_trailing_dot_is_parsed(self):
        event = self._by_date("2026-10-10")

        self.assertIsNotNone(event)
        self.assertEqual(event["city"], "Bendorf")
        self.assertEqual(event["end_date"], "2026-10-11")

    def test_badges_are_stripped_from_the_venue(self):
        for start_date, venue in (("2026-09-27", "Marktplatz"), ("2026-10-10", "Industriedenkmal Sayner Hütte")):
            with self.subTest(start_date=start_date):
                self.assertEqual(self._by_date(start_date)["venue"], venue)

    def test_badged_location_never_shifts_onto_the_following_market(self):
        """Regression: a discarded location item invented a Bonn market.

        "Aachen ... WIEDER DA!!!" was treated as a standalone badge, so the date
        inherited the *next* market's town and published a Bonn Friedensplatz
        market on 3.-7. June that does not exist.
        """
        multi_day = self._by_date("2026-06-03")

        self.assertEqual(multi_day["city"], "Aachen")
        self.assertNotEqual(multi_day["city"], "Bonn")

    def test_out_of_radius_towns_are_dropped(self):
        cities = {event["city"] for event in self._events()}

        self.assertNotIn("Bad Schwalbach", cities)

    def test_slash_region_uses_actual_municipality_and_core_venue(self):
        event = self._by_date("2026-12-13")

        self.assertIsNotNone(event)
        self.assertEqual(event["city"], "Mülheim-Kärlich")
        self.assertEqual(event["venue"], "CORE Eventlocation")

    def test_events_use_a_stable_source_id(self):
        self.assertTrue(all(event["source_id"] == "rhein-antik" for event in self._events()))

    def test_titles_dedupe_against_the_press_calendar_market_name(self):
        """The organizer record must collapse with the same civic occurrence."""
        organizer = self._by_date("2026-08-16")
        assert organizer is not None
        organizer["venue_id"] = "friedensplatz-bonn"
        organizer["category_key"] = "market"
        civic = {
            **organizer,
            "title": "Antikmarkt Bonn",
            "source": "Cölln Konzept",
            "score": 0.9,
        }

        self.assertTrue(report.events_are_duplicates(organizer, civic))
        self.assertEqual(len(report.deduplicate([organizer, civic])), 1)


if __name__ == "__main__":
    unittest.main()
