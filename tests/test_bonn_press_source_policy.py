import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.identity import event_id
from nrw_events.sources import bonn

from tests.helpers import patch_window


class BonnPressSourcePolicyTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 2), datetime(2026, 10, 31))

    @staticmethod
    def overview(**updates):
        return {
            "title": "Tag der Feuerwehr", "date": "2026-10-17",
            "start_date": "2026-10-17", "end_date": "2026-10-17",
            "city": "Bonn", "venue": "Münsterplatz", "time": "",
            "source": "Bonn district festivals", "source_id": "bonn-district-festivals",
            "link": "https://www.bonn.de/pressemitteilungen/jahresplanung.php",
            "link_kind": "overview", "score": 100,
            "description": "Lange allgemeine Jahresplanung. " * 20,
            **updates,
        }

    def test_annual_overview_discovery_variants_have_the_same_lowest_authority(self):
        for source in ("Bonn district festivals", "Bonn district festivals (Beuel.net discovery)"):
            self.assertEqual(report.source_authority(source), 0)
            self.assertLess(report.source_authority(source), report.source_authority("Bonn.jetzt"))

    def test_every_other_source_wins_and_keeps_its_copy_in_both_orders(self):
        overview = self.overview()
        for source in ("Bonn.de Events", "Eventbrite NRW", "Bonn.jetzt", "Cölln Konzept"):
            other = {
                **overview, "source": source, "source_id": "other-calendar",
                "score": 0.1, "link": "https://example.org/fire-brigade",
                "link_kind": "detail", "time": "10:00–17:00",
                "description": "Aktuelles Programm.",
            }
            for rows in ([overview, other], [other, overview]):
                with self.subTest(source=source, first=rows[0]["source"]):
                    [winner] = report.deduplicate(rows)
                    self.assertEqual(winner["source"], source)
                    self.assertEqual(winner["link"], other["link"])
                    self.assertEqual(winner["description"], other["description"])
                    self.assertEqual(winner["time"], other["time"])
                    self.assertIn(event_id(overview), winner["previous_event_ids"])

    def test_unique_overview_remains_and_unrelated_dates_are_not_merged(self):
        overview = self.overview()
        [winner] = report.deduplicate([overview])
        self.assertEqual(winner["source_id"], "bonn-district-festivals")
        other = {**overview, "source": "Eventbrite NRW", "source_id": "eventbrite",
                 "date": "2026-10-18", "start_date": "2026-10-18", "end_date": "2026-10-18"}
        self.assertEqual(len(report.deduplicate([overview, other])), 2)

    def test_vdk_source_block_covers_future_editions_but_not_other_events(self):
        for year in (2026, 2027):
            patch_window(self, datetime(year, 10, 1), datetime(year, 10, 31))
            html = f"""<ul>
              <li>Auftaktveranstaltung zur Haus- und Straßensammlung VdK, 28. Oktober {year}</li>
              <li>Anderer Sammlungsauftakt, Münsterplatz, 28. Oktober {year}</li>
            </ul>"""
            with patch.object(common, "fetch_url", return_value=html):
                rows = bonn.fetch_press_festivals()
            self.assertEqual([e["title"] for e in rows], ["Anderer Sammlungsauftakt"])
        independent = self.overview(
            title="Auftaktveranstaltung zur Haus- und Straßensammlung VdK",
            source="Volksbund", source_id="volksbund",
        )
        [winner] = report.deduplicate([independent])
        self.assertEqual(winner["source_id"], "volksbund")

    def test_antik_correction_matches_existing_primary_and_keeps_old_url(self):
        html = """<ul><li>Antik-, Kunst- &amp; Designmarkt Bonn, Friedensplatz,
          Bottlerplatz, 11. Oktober 2026, Rhein-Antik</li></ul>"""
        with patch.object(common, "fetch_url", return_value=html):
            [corrected] = bonn.fetch_press_festivals()
        self.assertEqual(corrected["title"], "Antikmarkt Bonn")
        self.assertEqual(corrected["start_date"], "2026-10-18")
        self.assertEqual(corrected["end_date"], "2026-10-18")
        self.assertEqual(corrected["time"], "11:00–17:00")
        self.assertEqual(corrected["source_id"], "c-lln-konzept")
        self.assertEqual(corrected["venue"], "Friedensplatz")
        self.assertEqual(event_id(corrected), "antikmarkt-bonn-8aa0363a00")
        primary = {**corrected, "previous_event_ids": [],
                   "description": "Primäres Marktprogramm mit geprüften Zeiten und Ortsangaben." * 3}
        for rows in ([primary, corrected], [corrected, primary]):
            [winner] = report.deduplicate(rows)
            self.assertEqual(event_id(winner), "antikmarkt-bonn-8aa0363a00")
            self.assertIn("antik-kunst-designmarkt-bonn-bba2e07428", winner["previous_event_ids"])
            self.assertNotIn("11. Oktober", winner["description"])

    def test_fire_brigade_has_reviewed_detail_facts_and_both_published_aliases(self):
        html = "<ul><li>Tag der Feuerwehr, 17. Oktober 2026</li></ul>"
        with patch.object(common, "fetch_url", return_value=html):
            [event] = bonn.fetch_press_festivals()
        self.assertEqual(event["source_id"], "bonn-de-events")
        self.assertEqual(event["link_kind"], "detail")
        self.assertEqual(event["time"], "10:00–17:00")
        self.assertEqual(event["venue"], "Münsterplatz")
        self.assertEqual(event["venue_address"], "Münsterplatz 1, 53111 Bonn")
        self.assertEqual(set(event["previous_event_ids"]), {
            "tag-der-feuerwehr-6945670d11", "tag-der-feuerwehr-2026-7ad2e56b3d",
        })
        self.assertEqual(event["discovered_via"], ["bonn-district-festivals"])

    def test_reviewed_2026_facts_do_not_reach_other_occurrences_or_years(self):
        patch_window(self, datetime(2027, 10, 1), datetime(2027, 10, 31))
        html = """<ul>
          <li>Antik-, Kunst- &amp; Designmarkt Bonn, Friedensplatz, 11. Oktober 2027</li>
          <li>Tag der Feuerwehr, 17. Oktober 2027</li>
        </ul>"""
        with patch.object(common, "fetch_url", return_value=html):
            rows = bonn.fetch_press_festivals()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(e["source"] == "Bonn district festivals" for e in rows))
        self.assertEqual(rows[0]["start_date"], "2027-10-11")
        self.assertTrue(all(not e["time"] for e in rows))
