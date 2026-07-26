"""Contract tests for the marktcom radius+format directory.

The fixture mirrors the real listing markup: ad blocks between results, the venue
in the ``eventname`` slot, the organizer in ``p.cat`` and the format encoded in the
badge icon path.
"""

import unittest
from datetime import datetime

from nrw_events import common, report
from nrw_events.sources import SOURCES, marktcom
from tests.helpers import patch_window


def _listing(*blocks):
    return "<ul class='list-unstyled marktliste w-100'>" + "".join(blocks) + "</ul>"


def _advert_block():
    return (
        "<li class='p-2'><div class='row'><div class='col-xs-12 w-100'>"
        "<div id='div-gpt-ad-1733335082565-2'></div>"
        "</div></div></li>"
    )


def _event_block(slug, event_name, postal, city, organizer, date, category_id,
                 description="Beschreibung des Marktes."):
    return (
        "<li class='p-2'><div class='row'><div class='col'><div class='row'>"
        "<div class='col-md-9 col-lg-9'>"
        f"<div class='eventname schmucklink'><a style=\"\" href=\"/veranstaltung/{slug}\">"
        f"{event_name}</a></div>"
        f"<div class='d-md-none'>{postal} {city}</div>"
        f"<p class='cat'>{organizer}</p>"
        f"<p class='description d-none d-md-block'>{description}"
        f"<a href=\"/veranstaltung/{slug}\">[mehr]</a></p>"
        "</div><div class='col-12'>"
        f"<div class='badge badge-pill badge-primary'><i class='far fa-calendar'></i>{date}</div>"
        "<div class='badge badge-pill mt-1' style='background-image:"
        f"url(/system/icons/{category_id}/original/vase.svg?1591042348)'>"
        f"<span>Kategorie</span></div>"
        "</div></div></div></div></li>"
    )


FIXTURE = _listing(
    _event_block("hit-markt-in-53757-sankt-augustin", "Hit-Markt", "53757",
                 "Sankt Augustin", "Geide-Märkte", "26.07.2026", 42),
    _advert_block(),
    _event_block("pferderennbahn-in-50737-koeln", "Pferderennbahn Parkplatz", "50737",
                 "Köln", "Melan macht Märkte", "29.07.2026", 42),
    _event_block("antik-troedelmarkt-in-53177-bonn", "Antik- und Trödelmarkt Bad Godesberg",
                 "53177", "Bonn", "Marktveranstaltungen Nikolopoulos", "02.08.2026", 42),
    _event_block("antikmarkt-in-53111-bonn", "Friedensplatz", "53111", "Bonn",
                 "Rhein-Antik Höderath", "16.08.2026", 42),
    _event_block("wochenmarkt-in-53111-bonn", "Marktplatz", "53111", "Bonn",
                 "Stadt Bonn", "03.08.2026", 31),
    _event_block("troedel-in-99999-hintertupfingen", "Festplatz", "99999",
                 "Hintertupfingen", "Irgendwer", "04.08.2026", 42),
)


class MarktcomSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 26), datetime(2026, 8, 23))

    def _events(self):
        return marktcom.events_from_listing(FIXTURE, 42)

    def test_source_is_registered(self):
        self.assertIs(SOURCES["marktcom"], marktcom.fetch)

    def test_listing_url_scopes_by_bonn_radius_and_format(self):
        url = marktcom.listing_url(42)

        self.assertIn(f"lat={common.BONN_LAT}", url)
        self.assertIn(f"radius={common.MAX_RADIUS_KM}", url)
        self.assertIn("q%5Bevent_kategorie_eq%5D=42", url)
        self.assertNotIn("page=", url)
        self.assertIn("page=3", marktcom.listing_url(42, 3))

    def test_produce_market_formats_are_never_requested(self):
        """The whole point of this source: exclude by format, not by keyword."""
        for excluded in (31, 43, 16, 41, 44):
            with self.subTest(category=excluded):
                self.assertNotIn(excluded, marktcom.WANTED_CATEGORIES)

    def test_advert_blocks_are_skipped(self):
        self.assertTrue(all(event.get("title") for event in self._events()))

    def test_record_filed_under_an_unwanted_format_is_dropped(self):
        """Trust the badge icon over the requested category id."""
        cities = [(event["city"], event["start_date"]) for event in self._events()]

        self.assertNotIn(("Bonn", "2026-08-03"), cities)

    def test_markets_of_already_integrated_organizers_are_dropped(self):
        """A directory copy of a first-party organizer adds no coverage."""
        venues = {event.get("venue") for event in self._events()}

        self.assertNotIn("Hit-Markt", venues)       # Geide-Märkte
        self.assertNotIn("Friedensplatz", venues)   # Rhein Antik

    def test_every_skip_marker_is_matched_case_and_spacing_insensitively(self):
        for organizer in ("GEIDE-MÄRKTE", "Grote  &  Hiller", "cölln konzept",
                          "Rhein-Antik Höderath", "Lampert Märkte GmbH"):
            with self.subTest(organizer=organizer):
                self.assertTrue(marktcom._is_integrated_organizer(organizer))

    def test_new_organizers_are_kept(self):
        for organizer in ("Melan macht Märkte", "Marktveranstaltungen Nikolopoulos",
                          "Trödelfabrik Bonn", "Stadt Neuwied"):
            with self.subTest(organizer=organizer):
                self.assertFalse(marktcom._is_integrated_organizer(organizer))

    def test_unknown_town_is_not_coerced_into_bonn(self):
        cities = {event["city"] for event in self._events()}

        self.assertNotIn("Hintertupfingen", cities)
        self.assertNotIn("Bonn", {c for c in cities if c == "Hintertupfingen"})

    def test_venue_is_kept_in_the_title_to_avoid_same_day_collisions(self):
        event = next(e for e in self._events() if e["city"] == "Köln")

        self.assertEqual(event["venue"], "Pferderennbahn Parkplatz")
        self.assertIn("Pferderennbahn", event["title"])
        self.assertIn("Antik-Trödelmarkt", event["title"])

    def test_marketing_prose_is_never_used_as_a_title(self):
        for event in self._events():
            with self.subTest(title=event["title"]):
                self.assertNotIn("Beschreibung des Marktes", event["title"])

    def test_directory_record_loses_to_the_district_publisher(self):
        """The Bad Godesberg antique market already arrives first hand."""
        directory = next(e for e in self._events() if e["start_date"] == "2026-08-02")
        district = {
            **directory,
            "title": "Antik- und Trödelmarkt",
            "city": "Bonn-Bad Godesberg",
            "source": "Bad Godesberg Stadtmarketing",
            "link": "https://bad-godesberg.info/antikmarkt",
            "score": 0.5,
        }

        self.assertTrue(report.events_are_duplicates(directory, district))
        deduped = report.deduplicate([directory, district])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Bad Godesberg Stadtmarketing")

    def test_recurring_series_dates_all_survive_dedup(self):
        events = self._events()

        self.assertEqual(len(report.deduplicate(events)), len(events))


class MarktcomPaginationTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 26), datetime(2026, 8, 23))

    def test_pagination_stops_once_a_page_starts_after_the_window(self):
        beyond = _listing(
            _event_block("x-in-50737-koeln", "Platz", "50737", "Köln",
                         "Melan macht Märkte", "01.10.2026", 42))

        self.assertTrue(marktcom._page_starts_after_window(beyond))
        self.assertFalse(marktcom._page_starts_after_window(FIXTURE))

    def test_next_page_detection_reads_the_pager_links(self):
        html = "<a href='/termine/radius?page=2&amp;radius=75'>2</a>"

        self.assertTrue(marktcom._has_page(html, 2))
        self.assertFalse(marktcom._has_page(html, 3))


if __name__ == "__main__":
    unittest.main()
