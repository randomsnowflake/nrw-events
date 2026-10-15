import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events import common
from nrw_events.civic_list_fallbacks import partition_city_list_fallbacks
from nrw_events.sources import bonn_martinszuege
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window

FIXTURE = Path(__file__).parent / "fixtures/bonn-martinszuege/martinszuege-2026-10-15.html"


def _organiser(**fields):
    # Shapes of the BV Holzlar / Beuel records in the 2026-10-15 production import.
    return canonicalize_event({
        "source": "BV Holzlar", "source_id": "bv-holzlar", "score": 5.0,
        "all_day": True, "start_at": "", "end_date": fields["start_date"], **fields,
    })


class BonnMartinszuegeTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 15), datetime(2026, 11, 30))
        self.events = bonn_martinszuege.events_from_page(FIXTURE.read_text(encoding="utf-8"))
        self.by_title = {event["title"]: event for event in self.events}

    def test_every_public_row_becomes_a_kids_event(self):
        # 66 rows on bonn.de, one marked "Schulveranstaltung".
        self.assertEqual(len(self.events), 65)
        self.assertEqual({event["category_key"] for event in self.events}, {"kids"})
        self.assertEqual({event["source_id"] for event in self.events}, {"bonn-martinszuege"})
        self.assertFalse(any("Donatusschule" in event["title"] for event in self.events))

    def test_row_keeps_time_start_and_destination(self):
        event = self.by_title["Martinszug Bonn Zentral"]
        self.assertEqual(event["start_at"], "2026-11-04T16:30+01:00")
        self.assertEqual(event["venue"], "Münsterplatz")
        self.assertEqual(event["description"], "Start: Münsterplatz. Ziel: Markt.")
        self.assertEqual(event["link"], bonn_martinszuege.URL)

    def test_explicit_start_wins_over_assembly_time(self):
        self.assertEqual(self.by_title["Martinszug Bonn-Castell"]["start_at"], "2026-11-03T17:30+01:00")

    def test_district_comes_from_the_row_not_only_the_stadtbezirk(self):
        self.assertEqual(self.by_title["Martinszug Holzlar"]["city"], "Bonn-Holzlar")
        self.assertEqual(self.by_title["Martinszug Duisdorf"]["city"], "Bonn-Duisdorf")
        self.assertEqual(self.by_title["Martinszug Pennenfeld"]["city"], "Bonn-Bad Godesberg")
        self.assertEqual(self.by_title["Großer Martinszug Beuel-Mitte"]["city"], "Bonn-Beuel")

    def test_page_without_tables_is_a_healthy_off_season(self):
        self.assertTrue(bonn_martinszuege._no_list_published("<h2>Martinszüge</h2><p>Karte folgt.</p>"))
        self.assertFalse(bonn_martinszuege._no_list_published(FIXTURE.read_text(encoding="utf-8")))

    def test_fetch_reads_the_city_page(self):
        with patch.object(common, "fetch_url", return_value=FIXTURE.read_text(encoding="utf-8")):
            events = bonn_martinszuege.fetch()
        self.assertEqual(len(events), 65)


class CityListFallbackTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 15), datetime(2026, 11, 30))
        events = bonn_martinszuege.events_from_page(FIXTURE.read_text(encoding="utf-8"))
        self.rows = {event["title"]: canonicalize_event(event) for event in events}

    def test_organiser_record_replaces_the_city_row_and_inherits_its_id(self):
        holzlar = _organiser(
            title="Martinszug Holzlar", start_date="2026-11-04", city="Bonn-Holzlar",
            venue="Holzlar / Kirchwiese", link="https://bv-holzlar.de/veranstaltung/martinszug-holzlar/",
        )
        row = self.rows["Martinszug Holzlar"]
        for events in ([row, holzlar], [holzlar, row]):
            kept, replaced = partition_city_list_fallbacks(events)
            self.assertEqual([event.source_id for event in kept], ["bv-holzlar"])
            self.assertEqual(replaced, [row])
            self.assertTrue(set(kept[0].previous_event_ids))

    def test_title_variants_of_one_procession_are_folded(self):
        roleber = _organiser(
            title="Martinszug Roleber", start_date="2026-11-06", city="Bonn-Roleber",
            venue="Roleber", link="https://bv-holzlar.de/veranstaltung/martinszug-roleber/",
        )
        kept, _ = partition_city_list_fallbacks([self.rows["Martinszug Bürgerverein Roleber-Gielgen e.V."], roleber])
        self.assertEqual([event.source_id for event in kept], ["bv-holzlar"])

    def test_other_processions_on_the_same_day_stay(self):
        beuel = _organiser(
            title="Großer Sankt Martinszug ab Ringstr", start_date="2026-11-05", city="Bonn-Beuel",
            venue="Ringstr", source="sankt-martin-beuel.de", source_id="beuel-net",
            link="https://www.sankt-martin-beuel.de/",
        )
        taubenschlag = self.rows["Martinszug KiTa Taubenschlag"]
        kept, replaced = partition_city_list_fallbacks([taubenschlag, beuel])
        self.assertEqual(replaced, [])
        self.assertEqual(len(kept), 2)

    def test_one_organiser_record_never_absorbs_two_rows(self):
        ippendorf = _organiser(
            title="Martinszug Ippendorf", start_date="2026-11-04", city="Bonn",
            venue="Ippendorf", link="https://example.test/ippendorf",
        )
        second = canonicalize_event({**self.rows["Martinszug Ippendorf II"].to_dict(), "start_date": "2026-11-04",
                                     "end_date": "2026-11-04", "start_at": "2026-11-04T18:30+01:00"})
        kept, replaced = partition_city_list_fallbacks([self.rows["Martinszug Ippendorf"], second, ippendorf])
        self.assertEqual(replaced, [])
        self.assertEqual(len(kept), 3)

    def test_aggregator_cards_do_not_replace_the_city_row(self):
        card = _organiser(
            title="Martinszug Holzlar", start_date="2026-11-04", city="Bonn-Holzlar",
            source="Bonn.jetzt", source_id="bonn-jetzt", link="https://bonn.jetzt/event/martinszug-holzlar",
        )
        kept, replaced = partition_city_list_fallbacks([self.rows["Martinszug Holzlar"], card])
        self.assertEqual(replaced, [])
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
