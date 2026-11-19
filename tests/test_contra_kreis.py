import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events.sources import SOURCE_IDS, SOURCES, contra_kreis

from tests.helpers import patch_window

CALENDAR_HTML = (Path(__file__).parent / "fixtures" / "contra-kreis" / "embedded-calendar.html").read_text(
    encoding="utf-8"
)


class ContraKreisSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 12, 1), datetime(2026, 12, 31))

    def test_calendar_retains_source_identity_and_visit_facts(self):
        premiere = contra_kreis.events_from_calendar(CALENDAR_HTML)[0]
        self.assertTrue(premiere["description"].startswith("Premiere."))
        self.assertIn("Besetzung:", premiere["description"])
        self.assertEqual(premiere["venue"], "Contra-Kreis-Theater")
        self.assertIn("Am Hof 3-5", premiere["venue_address"])
        self.assertEqual(
            premiere["link"], "https://www.contra-kreis-theater.de/veranstaltung/und-taeglich-gruent-der-tannenbaum/"
        )
        self.assertEqual(premiere["category_key"], "stage")
        self.assertEqual(premiere["source_id"], "contra-kreis-theater")

    def test_plot_mentioning_children_keeps_the_play_above_the_publication_floor(self):
        plot = CALENDAR_HTML.replace(
            '"excerpt": "Für Götz',
            '"excerpt": "Ihr Mann will, dass sie Kinder kriegt. Für Götz',
        )
        premiere = contra_kreis.events_from_calendar(plot)[0]
        self.assertIn("Kinder", premiere["description"])
        self.assertGreaterEqual(premiere["score"], 0.45)

    def test_embedded_calendar_keeps_both_new_years_eve_performances(self):
        with patch("nrw_events.common.fetch_url", return_value=CALENDAR_HTML) as fetch:
            events = contra_kreis.fetch()
        fetch.assert_called_once_with("https://www.contra-kreis-theater.de/termine/", timeout=25)
        self.assertEqual([e["start_at"] for e in events], [
            "2026-12-01T19:30+01:00", "2026-12-31T17:00+01:00", "2026-12-31T20:00+01:00",
        ])
        self.assertEqual({e["title"] for e in events}, {"Und täglich grünt der Tannenbaum"})
        self.assertEqual({e["source_id"] for e in events}, {"contra-kreis-theater"})

    def test_source_badges_preserve_cancelled_and_sold_out(self):
        withdrawn = CALENDAR_HTML.replace('"status": "normal"', '"status": "faellt_aus"', 1)
        sold_out = CALENDAR_HTML.replace('"status": "normal"', '"status": "ausverkauft"', 1)
        self.assertEqual(contra_kreis.events_from_calendar(withdrawn)[0]["status"], "cancelled")
        self.assertEqual(contra_kreis.events_from_calendar(sold_out)[0]["availability"], "SoldOut")

    def test_missing_or_damaged_payload_is_not_healthy_empty(self):
        for page in ('<html></html>', CALENDAR_HTML.replace('"19:30"', '"99:99"')):
            with self.subTest(page=page[:30]), self.assertRaises(ValueError):
                contra_kreis.events_from_calendar(page)

    def test_source_is_registered_with_stable_id(self):
        self.assertIs(SOURCES["Contra-Kreis-Theater"], contra_kreis.fetch)
        self.assertEqual(SOURCE_IDS["Contra-Kreis-Theater"], "contra-kreis-theater")


if __name__ == "__main__":
    unittest.main()
