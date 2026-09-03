"""Contract tests for the Cölln Antik&Design organizer schedule.

The fixture reproduces the real hand-maintained markup, including the address line
nested beside the market name, several days joined by ``+`` and ``und``, holiday
weekday prefixes, a street number range that precedes the opening hours, and a
typo'd year.
"""

import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common
from nrw_events.health import SourceResult, SourceStatus
from nrw_events.sources import SOURCES
from nrw_events.sources import coelln_antik_design as cad

from tests.helpers import patch_window


def _section(name, detail, *dates):
    items = "".join(f"<li>{date}</li>" for date in dates)
    return (
        f'<p><span style="color: #800000;"><strong>{name}<br /></strong>'
        f"{detail}</span></p><ul>{items}<li> </li></ul>"
    )


FIXTURE = (
    '<div class="entry-content">'
    + _section("Antik- und Designmarkt in der Kölner Flora",
               "Am Botanischen Garten 1a 50375 Köln, 11 &#8211; 18 Uhr, Eintritt  6 EUR",
               "Sonntag 15. März 2026", "Sonntag 20. September 2026")
    + _section("Antik-Designmarkt im Kölner Gürzenich",
               "Martinstraße 27-38 50667 Köln, 11 &#8211; 18 Uhr, Eintritt 6 EUR",
               "Sonntag 15. November 2026", "Sonntag 27. Dezember 222026")
    + _section("Antik- und Designmarkt auf dem Kölner Neumarkt",
               "50667 Köln, 11 &#8211; 18",
               "Freitag 20. +  Samstag 21. + Sonntag 22. März 2026")
    + _section("Lifestyle-Markt Kölner Rheinauhafen",
               "50678 Köln von 11 &#8211; 18",
               "Ostersonntag  05. April und Ostermontag 06. April 2026")
    + _section("Markt im Nirgendwo", "99999 Hintertupfingen, 11 &#8211; 18",
               "Sonntag 12. Juli 2026")
    + "</div><!-- .entry-content -->"
)


class CoellnAntikDesignTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 1, 1), datetime(2026, 12, 31))

    def _events(self):
        return cad.events_from_page(FIXTURE)

    def _by_date(self, start_date):
        return next((e for e in self._events() if e["start_date"] == start_date), None)

    def test_source_is_registered(self):
        self.assertIs(SOURCES["Cölln Antik&Design"], cad.fetch)

    def test_market_name_price_and_city_are_split_out(self):
        event = self._by_date("2026-03-15")

        self.assertEqual(event["title"], "Antik- und Designmarkt in der Kölner Flora")
        self.assertEqual(event["city"], "Köln")
        self.assertEqual(event["price"], "6 EUR")

    def test_hour_range_survives_a_leading_street_number_range(self):
        """Regression: "Martinstraße 27-38" matched before the opening hours."""
        event = self._by_date("2026-11-15")

        self.assertEqual(event["time"], "11:00–18:00")

    def test_hour_range_helper_rejects_impossible_hours(self):
        self.assertEqual(cad._hour_range("Martinstraße 27-38, 11 – 18 Uhr"),
                         "11:00–18:00")
        self.assertEqual(cad._hour_range("Hausnummer 27-38"), "")
        self.assertEqual(cad._hour_range("kein Zeitbereich"), "")

    def test_plus_joined_days_become_one_multi_day_event(self):
        event = self._by_date("2026-03-20")

        self.assertEqual(event["end_date"], "2026-03-22")

    def test_und_joined_holiday_days_become_one_event(self):
        event = self._by_date("2026-04-05")

        self.assertEqual(event["end_date"], "2026-04-06")

    def test_typo_year_is_dropped_with_a_warning_not_repaired(self):
        quality_skips = []
        with mock.patch.object(
            common,
            "log_source_quality_skip",
            side_effect=lambda source, reason: quality_skips.append(reason),
        ):
            dates = {event["start_date"] for event in self._events()}

        self.assertNotIn("2026-12-27", dates)
        self.assertTrue(any("222026" in reason for reason in quality_skips))

    def test_typo_year_is_a_quality_skip_not_a_degraded_source_warning(self):
        result = SourceResult("Cölln Antik&Design")
        with mock.patch.object(common, "_SOURCE_CONTEXT") as context:
            context.result = result
            events = cad.events_from_page(FIXTURE)
        result.finish(events)

        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.warnings, [])
        self.assertEqual(
            result.rejection_reasons,
            {"quality:implausible-year:222026": 1},
        )

    def test_implausible_year_bounds(self):
        self.assertTrue(cad._plausible_year(common.TODAY.year))
        self.assertTrue(cad._plausible_year(common.TODAY.year + 3))
        self.assertFalse(cad._plausible_year(common.TODAY.year + 4))
        self.assertFalse(cad._plausible_year(common.TODAY.year - 2))

    def test_unknown_town_is_not_coerced_into_a_known_city(self):
        cities = {event["city"] for event in self._events()}

        self.assertNotIn("Hintertupfingen", cities)
        self.assertNotIn("Markt im Nirgendwo",
                         {event["title"] for event in self._events()})

    def test_blank_list_items_are_ignored(self):
        self.assertTrue(all(event["start_date"] for event in self._events()))

    def test_every_parsed_event_carries_a_time(self):
        self.assertTrue(all(event["time"] for event in self._events()))

    def test_advertised_hours_populate_canonical_timestamps(self):
        single_day = self._by_date("2026-03-15")
        multi_day = self._by_date("2026-03-20")

        self.assertEqual(single_day["start_at"], "2026-03-15T11:00+01:00")
        self.assertEqual(single_day["end_at"], "2026-03-15T18:00+01:00")
        self.assertEqual(multi_day["start_at"], "2026-03-20T11:00+01:00")
        self.assertEqual(multi_day["end_at"], "2026-03-22T18:00+01:00")

    def test_events_use_a_stable_source_id(self):
        self.assertTrue(
            all(
                event["source_id"] == "coelln-antik-design"
                for event in self._events()
            )
        )


if __name__ == "__main__":
    unittest.main()
