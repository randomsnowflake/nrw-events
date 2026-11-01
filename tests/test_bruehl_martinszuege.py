import unittest
from datetime import datetime
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

from nrw_events import common
from nrw_events.duplicate_identity import events_are_duplicates
from nrw_events.sources import bruehl_martinszuege
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window

FIXTURE = Path(__file__).parent / "fixtures/bruehl-martinszuege/umzuege-2026-11-01.html"


class BruehlMartinszuegeTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 11, 1), datetime(2026, 11, 30))
        self.html = FIXTURE.read_text(encoding="utf-8")
        self.events = bruehl_martinszuege.events_from_page(self.html)
        self.by_title = {event["title"]: event for event in self.events}

    def test_every_listed_procession_becomes_a_kids_event_with_a_route(self):
        self.assertEqual(len(self.events), 14)
        self.assertEqual(len(self.by_title), 14)
        for event in self.events:
            self.assertEqual(event["category_key"], "kids")
            self.assertEqual(event["city"], "Brühl")
            self.assertTrue(event["venue"], event["title"])
            self.assertIn("Zugweg: ", event["description"])
            self.assertEqual(event["description_source"], "scraped")
            self.assertTrue(event["start_at"] and event["end_at"], event["title"])

    def test_row_keeps_time_span_start_and_route(self):
        event = self.by_title["Martinszug Innenstadt"]
        self.assertEqual(event["start_at"], "2026-11-04T17:30+01:00")
        self.assertEqual(event["end_at"], "2026-11-04T18:30+01:00")
        self.assertEqual(event["venue"], "Franziskanerhof/Leamington-Spa-Platz")
        intro, route, organizer = event["description"].split("\n\n")
        self.assertEqual(intro, "Laternenumzug in der Brühler Innenstadt von 17:30 bis 18:30 Uhr.")
        self.assertTrue(route.startswith("Zugweg: "))
        self.assertEqual(organizer, "Veranstalter: Stadt Brühl.")
        self.assertIn("Uhlstraße – Markt – Kölnstraße", event["description"])
        self.assertEqual(event["link"], bruehl_martinszuege.URL)

    def test_copy_has_no_doubled_full_stop(self):
        self.assertEqual([event["title"] for event in self.events if ".." in event["description"]], [])

    def test_plus_inside_an_organiser_name_does_not_split_the_item(self):
        event = self.by_title["Martinszug Familienzentrum Brühl-Süd"]
        self.assertEqual(event["start_at"], "2026-11-10T17:45+01:00")
        self.assertIn("Kita Auf der Pehle + Kita Lessingstraße", event["description"])

    def test_every_row_clears_the_publication_score_floor(self):
        low = [event["title"] for event in self.events if canonicalize_event(event).score < 0.4]
        self.assertEqual(low, [])

    def test_processions_never_merge_with_each_other(self):
        canonical = [canonicalize_event(event) for event in self.events]
        pairs = [(a.title, b.title) for a, b in combinations(canonical, 2) if events_are_duplicates(a, b)]
        self.assertEqual(pairs, [])

    def test_collective_calendar_entry_does_not_absorb_a_procession(self):
        # Shape of the sitekit-bruehl record; the website drops it, so a merge would lose the procession.
        collective = canonicalize_event({
            "title": "St. Martins-Umzüge im Stadtgebiet (03.11.-12.11.)", "start_date": "2026-11-03",
            "end_date": "2026-11-03", "all_day": True, "start_at": "", "city": "Brühl", "venue": "",
            "source": "SiteKit regional", "source_id": "sitekit-bruehl", "category_key": "kids", "score": 1.0,
            "link": bruehl_martinszuege.URL + "?sp%3AstartDateKey=2026-11-03",
        })
        merged = [event["title"] for event in self.events if events_are_duplicates(collective, canonicalize_event(event))]
        self.assertEqual(merged, [])

    def test_unknown_procession_publishes_with_the_page_facts_only(self):
        html = self.html.replace("DG Kierberg", "Neuer Verein")
        event = next(e for e in bruehl_martinszuege.events_from_page(html) if e["title"] == "Martinszug Neuer Verein")
        self.assertEqual(event["venue"], "")
        self.assertEqual(event["description"], "Veranstalter: Neuer Verein.")
        self.assertEqual(event["description_source"], "generated")

    def test_page_without_overview_is_a_healthy_off_season(self):
        self.assertTrue(bruehl_martinszuege._no_list_published("<h3>St. Martins-Umzüge</h3>"))
        self.assertFalse(bruehl_martinszuege._no_list_published(self.html))
        self.assertEqual(bruehl_martinszuege.events_from_page("<h3>St. Martins-Umzüge</h3>"), [])

    def test_fetch_reads_the_city_page(self):
        with patch.object(common, "fetch_url", return_value=self.html):
            self.assertEqual(len(bruehl_martinszuege.fetch()), 14)
