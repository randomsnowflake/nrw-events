import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import bonnkirmes, regional_html

BONNKIRMES_HTML = """
<html><body>
  <h3>Herbstkirmes Duisdorf</h3>
  <p>Jedes Jahr am ersten Wochenende im September findet auf dem Europaplatz an der Rochusstraße die traditionelle Herbstkirmes statt. Im Jahr 2025 findet die Kirmes vom 04.09.2026 bis zum 07.09.2026 statt.</p>
</body></html>
"""

BORNHEIM_HTML = """
<article class="event-teaser">
  <span class="date-card-btn-date">29.08.2026</span>
  <span class="date-card-btn-date">30.08.2026</span>
  <span class="eventcategory">Vereine &amp; Brauchtum</span>
  <p class="event-title">Dorffest / Kirmes in Widdig</p>
  <a href="/freizeit-tourismus/veranstaltungen/veranstaltungskalender/veranstaltung/veranstaltung/dorffest-kirmes-widdig">Details</a>
</article>
<nav>1 2 3 4 5 … 7</nav>
<p>Hier können Sie eine neue Veranstaltung in den Kalender eintragen.</p>
<p>Stadt Bornheim Newsletter Jetzt anmelden Rathaus &amp; Service</p>
"""


def event(**overrides):
    base = {
        "title": "Kirmes",
        "start_date": "2026-08-28",
        "end_date": "2026-08-30",
        "start_at": "",
        "end_at": "",
        "city": "Bonn",
        "venue": "",
        "source": "Direct publisher",
        "source_id": "direct",
        "category_key": "festival",
        "event_types": ["funfair"],
        "description": "",
        "score": 1.0,
    }
    return {**base, **overrides}


class KirmesDataQualityTests(unittest.TestCase):
    def setUp(self):
        self.today = patch.object(common, "TODAY", datetime(2026, 1, 1))
        self.end = patch.object(common, "END_DATE", datetime(2026, 12, 31))
        self.today.start()
        self.end.start()

    def tearDown(self):
        self.end.stop()
        self.today.stop()

    def test_bonnkirmes_reconciles_stale_prose_year_with_parsed_schedule(self):
        [event_row] = bonnkirmes.events_from_html(BONNKIRMES_HTML, strict=True)

        self.assertIn("Im Jahr 2026 findet", event_row["description"])
        self.assertNotIn("Im Jahr 2025", event_row["description"])

    def test_bornheim_listing_does_not_publish_calendar_navigation_as_description(self):
        rows = regional_html._events_from_bornheim(BORNHEIM_HTML)

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["description"] for row in rows))
        self.assertTrue(all(row["description_source"] == "generated" for row in rows))
        self.assertTrue(all("Hier können Sie" not in row["description"] for row in rows))
        self.assertTrue(all("Newsletter" not in row["description"] for row in rows))

    def test_funfair_title_year_and_locative_word_do_not_block_same_run_dedup(self):
        lupe = event(
            title="Kirmes in Röttgen 2026",
            end_date="2026-08-31",
            source="LuPe Events",
            source_id="lupe-events",
        )
        civic = event(
            title="Kirmes Röttgen",
            venue="Herzogsfreudenweg",
            source="Bonn district festivals",
            source_id="bonn-district-festivals",
        )

        self.assertTrue(report.events_are_duplicates(lupe, civic))
        self.assertEqual(len(report.deduplicate([lupe, civic])), 1)

    def test_tourism_calendar_daily_card_merges_into_exact_official_funfair_run(self):
        official = event(
            title="Kirmes in Sinzig",
            start_date="2026-08-14",
            end_date="2026-08-18",
            city="Sinzig",
            venue="Innenstadt",
            source="ionas4 regional",
            source_id="ionas4-regional",
        )
        tourism_card = event(
            title="Kirmes in Sinzig",
            start_date="2026-08-15",
            end_date="2026-08-15",
            city="Sinzig",
            source="Ahrtal",
            source_id="ahrtal",
        )

        self.assertTrue(report.events_are_duplicates(official, tourism_card))
        self.assertEqual(len(report.deduplicate([official, tourism_card])), 1)
        self.assertEqual(report.source_authority("Ahrtal"), 2)
        self.assertEqual(report.source_authority("Ahrtal official organizer"), 3)

    def test_distinct_funfairs_with_conflicting_concrete_venues_remain_distinct(self):
        left = event(title="Kirmes Bonn 2026", venue="Dorfplatz Nord")
        right = event(title="Kirmes in Bonn", venue="Dorfplatz Süd", source="Other publisher")

        self.assertFalse(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 2)

    def test_distinct_house_numbers_are_a_concrete_venue_conflict(self):
        left = event(title="Kirmes Bonn 2026", venue="Hauptstraße 10")
        right = event(title="Kirmes in Bonn", venue="Hauptstraße 100", source="Other publisher")

        self.assertFalse(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 2)

    def test_address_only_house_numbers_remain_distinct_after_normalization(self):
        left = event(title="Kirmes Bonn 2026", venue="")
        left["venue_address"] = "Hauptstraße 10, 53111 Bonn"
        right = event(title="Kirmes in Bonn", venue="", source="Other publisher")
        right["venue_address"] = "Hauptstraße 100, 53111 Bonn"

        self.assertFalse(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 2)

    def test_named_venues_still_compare_their_addresses(self):
        left = event(venue="Festplatz")
        left["venue_address"] = "Hauptstraße 10, 53111 Bonn"
        right = event(venue="Festplatz", source="Other publisher")
        right["venue_address"] = "Hauptstraße 100, 53111 Bonn"

        self.assertFalse(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 2)

    def test_equivalent_address_range_dash_variants_do_not_conflict(self):
        left = event(title="Kirmes Bonn 2026", venue="Festplatz")
        left["venue_address"] = "Alfterer Straße 35–37, 53121 Bonn"
        right = event(
            title="Kirmes in Bonn",
            venue="Festplatz",
            source="Other publisher",
        )
        right["venue_address"] = "Alfterer Straße 35-37, 53121 Bonn"

        self.assertTrue(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 1)

    def test_reviewed_venue_aliases_ignore_address_enrichment(self):
        left = event(title="Kirmes Bonn 2026", venue="Möhneplatz")
        left["venue_address"] = "Friedrich-Breuer-Straße 65, 53225 Bonn"
        right = event(
            title="Kirmes in Bonn",
            venue="Beueler Rathaus",
            source="Other publisher",
        )
        right["venue_address"] = "Friedrich-Breuer-Straße 65, 53225 Bonn"

        self.assertTrue(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 1)

    def test_same_source_does_not_override_conflicting_address_units(self):
        for venue, left_number, right_number in (
            ("Festplatz", "10a", "10b"),
            ("Festplatz", "10/1", "100/1"),
            ("Festplatz", "10-1", "100-1"),
            ("Halle 1", "10a", "10b"),
            ("Halle 1", "10/1", "100/1"),
            ("Halle 1", "10-1", "100-1"),
        ):
            with self.subTest(venue=venue, left=left_number, right=right_number):
                left = event(venue=venue)
                left["venue_address"] = f"Hauptstraße {left_number}, 53111 Bonn"
                right = event(venue=venue)
                right["venue_address"] = f"Hauptstraße {right_number}, 53111 Bonn"

                self.assertFalse(report.events_are_duplicates(left, right))
                self.assertEqual(len(report.deduplicate([left, right])), 2)

    def test_bonnkirmes_repairs_only_the_sentence_with_the_exact_start_date(self):
        html = BONNKIRMES_HTML.replace(
            "Im Jahr 2025 findet die Kirmes vom 04.09.2026 bis zum 07.09.2026 statt.",
            "Im Jahr 2025 findet die Kirmes erstmals auf dem alten Platz statt. "
            "Im Jahr 2025 findet die Kirmes vom 04.09.2026 bis zum 07.09.2026 statt.",
        )

        [parsed] = bonnkirmes.events_from_html(html)

        self.assertIn("Im Jahr 2025 findet die Kirmes erstmals", parsed["description"])
        self.assertIn("Im Jahr 2026 findet die Kirmes vom 04.09.2026", parsed["description"])

    def test_non_funfair_years_remain_identity_tokens(self):
        left = event(title="Jahresausstellung 2025", event_types=[], category_key="exhibition")
        right = event(title="Jahresausstellung 2026", event_types=[], category_key="exhibition", source="Other publisher")

        self.assertFalse(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 2)

    def test_generic_funfair_title_without_a_place_is_not_an_identity(self):
        left = event(title="Kirmes 2026")
        right = event(title="Kirmes", source="Other publisher")

        self.assertFalse(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 2)


if __name__ == "__main__":
    unittest.main()
