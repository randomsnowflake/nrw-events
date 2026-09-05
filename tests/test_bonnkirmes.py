import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import SOURCES, bonnkirmes
from nrw_events.sources import regional_common as rc
from nrw_events.validation import canonicalize_event

PAGE_HTML = """
<html><body>
  <h2>Unsere Volksfeste</h2>
  <h3><span>Osterkirmes Beuel</span></h3>
  <p>Jedes Jahr rund um Ostern am Beueler Rheinufer vom „Bahnhöfchen“ bis zum Anleger des "China-Schiffs". Im Jahr 2026 findet die Kirmes vom 27.03. – 12.04.2026 statt - Karfreitag ist geschlossen.</p>
  <p>Öffnungszeiten: Montag bis Samstag 14 Uhr bis 22 Uhr, Sonntag 11 Uhr - ca. 22 Uhr.</p>
  <h3>Kirmes Bad Godesberg</h3>
  <p>Jedes Jahr im Frühjahr auf der Rigal'schen Wiese in Bad Godesberg findet die große Kirmes statt. Dieses Jahr findet die Kirmes vom 17.04. – 26.04.2026 statt.</p>
  <h3>Maikirmes Duisdorf</h3>
  <p>Jedes Jahr rund um den 1. Mai herum findet auf dem Europaplatz an der Rochusstraße die traditionelle Maikirmes statt. Im Jahr 2025 findet die Kirmes vom 30.04.2026 bis zum 04.05.2026 statt.</p>
  <h3>Herbstkirmes Duisdorf</h3>
  <p>Jedes Jahr am ersten Wochenende im September findet auf dem Europaplatz an der Rochusstraße die traditionelle Herbstkirmes statt. Im Jahr 2025 findet die Kirmes vom 04.09.2026 bis zum 07.09.2026 statt.</p>
</body></html>
"""


class BonnKirmesSourceTests(unittest.TestCase):
    def setUp(self):
        self.today = patch.object(common, "TODAY", datetime(2026, 1, 1))
        self.end = patch.object(common, "END_DATE", datetime(2026, 12, 31))
        self.today.start()
        self.end.start()

    def tearDown(self):
        self.end.stop()
        self.today.stop()

    def test_source_is_registered_as_a_first_party_adapter(self):
        self.assertIs(SOURCES["Kirmes in Bonn"], bonnkirmes.fetch)

    def test_parses_all_published_fairs_with_dates_and_specific_places(self):
        events = bonnkirmes.events_from_html(PAGE_HTML, strict=True)

        self.assertEqual(
            [(event["title"], event["start_date"], event["end_date"]) for event in events],
            [
                ("Osterkirmes Beuel", "2026-03-27", "2026-04-12"),
                ("Kirmes Bad Godesberg", "2026-04-17", "2026-04-26"),
                ("Maikirmes Duisdorf", "2026-04-30", "2026-05-04"),
                ("Herbstkirmes Duisdorf", "2026-09-04", "2026-09-07"),
            ],
        )
        self.assertEqual(
            [(event["city"], event["venue"]) for event in events],
            [
                ("Bonn-Beuel", "Beueler Rheinufer"),
                ("Bonn-Bad Godesberg", "Rigal'sche Wiese"),
                ("Bonn-Duisdorf", "Europaplatz an der Rochusstraße"),
                ("Bonn-Duisdorf", "Europaplatz an der Rochusstraße"),
            ],
        )
        self.assertTrue(all(event["source"] == "Kirmes in Bonn" for event in events))
        self.assertTrue(all(event["source_id"] == "bonnkirmes" for event in events))
        self.assertTrue(all(event["category_key"] == "festival" for event in events))
        self.assertEqual([event["link"] for event in events], [
            "https://www.bonnkirmes.com/",
            "https://www.bonnkirmes.com/",
            "https://www.bonnkirmes.com/",
            "https://www.ofa-duisdorf.de/kirmes",
        ])
        self.assertIn("Karfreitag ist geschlossen", events[0]["description"])
        self.assertIn("Öffnungszeiten", events[0]["description"])

    def test_filtered_rows_are_valid_candidates_not_parser_drift(self):
        with patch.object(bonnkirmes.common, "make_event", return_value=None):
            self.assertEqual(bonnkirmes.events_from_html(PAGE_HTML, strict=True), [])

    def test_duisdorf_square_aliases_merge_with_the_existing_civic_record(self):
        direct = bonnkirmes.events_from_html(PAGE_HTML, strict=True)[2]
        civic = {
            **direct,
            "title": "Maikirmes in Duisdorf",
            "venue": "Rochusplatz",
            "city": "Bonn",
            "source": "Bonn district festivals",
            "source_id": "bonn-district-festivals",
            "link": "https://www.bonn.de/pressemitteilungen/example.php",
        }

        canonical = [canonicalize_event(event) for event in (civic, direct)]
        self.assertEqual(
            {event["venue_id"] for event in canonical},
            {"duisdorf-rochusplatz-europaplatz"},
        )
        deduped = report.deduplicate(canonical)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Kirmes in Bonn")

    def test_keeps_complete_split_opening_hours_without_contact_boilerplate(self):
        html = """
        <h3>Herbstkirmes Duisdorf</h3>
        <p>Jedes Jahr findet die Herbstkirmes statt. Im Jahr 2025 findet die Kirmes vom 04.09.2026 bis zum 07.09.2026 statt.</p>
        <p>Öffnungszeiten: Samstag 12.00 bis 23.00 Uhr,</p>
        <p>Sonntag 11.00 bis 23.00 Uhr, Freitag und Montag 14.00 bis 23.00 Uhr.</p>
        <p>\u200b</p>
        <p>Please contact us by using this form:</p>
        <p>bonnarge@example.test</p>
        """

        [event] = bonnkirmes.events_from_html(html, strict=True)

        self.assertIn("Samstag 12.00 bis 23.00 Uhr", event["description"])
        self.assertIn("Sonntag 11.00 bis 23.00 Uhr", event["description"])
        self.assertIn("Freitag und Montag 14.00 bis 23.00 Uhr", event["description"])
        self.assertNotIn("Please contact", event["description"])
        self.assertNotIn("example.test", event["description"])

    def test_description_does_not_repeat_a_lead_paragraph_that_contains_selected_markers(self):
        description = bonnkirmes._description(
            [
                "Intro.",
                "Öffnungszeiten und Zum Schutz der Besucher gelten Regeln.",
                "Weiterer Hinweis.",
            ],
            datetime(2026, 9, 4),
        )

        self.assertEqual(description.count("Öffnungszeiten"), 1)
        self.assertEqual(description.count("Zum Schutz"), 1)

    def test_recognizable_page_without_dated_fair_sections_reports_parser_drift(self):
        with self.assertRaisesRegex(rc.ParserEmptyError, "dated fair sections"):
            bonnkirmes.events_from_html(
                "<h2>Unsere Volksfeste</h2><h3>Osterkirmes Beuel</h3><p>Termine folgen.</p>",
                strict=True,
            )


if __name__ == "__main__":
    unittest.main()
