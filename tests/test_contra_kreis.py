import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events.sources import SOURCE_IDS, SOURCES, contra_kreis

from tests.helpers import patch_window

MONTH_HTML = (Path(__file__).parent / "fixtures" / "contra-kreis" / "month-2026-10.html").read_text(
    encoding="utf-8"
)


class ContraKreisSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 1), datetime(2026, 10, 31))

    def test_month_fragment_yields_one_event_per_playing_day(self):
        requested = []

        def fake_post(url, fields, **_kwargs):
            requested.append(fields)
            self.assertEqual(url, contra_kreis._AJAX_URL)
            return MONTH_HTML

        with patch("nrw_events.http.post_form_text", side_effect=fake_post):
            events = contra_kreis.fetch()

        self.assertEqual(requested, [{"action": "cal", "data": "2026-10"}])
        self.assertEqual(
            [(event["title"], event["start_at"]) for event in events],
            [
                ("Rita will’s wissen", "2026-10-22T19:30+02:00"),
                ("Der Tatortreiniger – Neue Folgen", "2026-10-18T15:00+02:00"),
            ],
        )
        premiere = events[0]
        self.assertTrue(premiere["description"].startswith("Premiere."))
        self.assertIn("Besetzung:", premiere["description"])
        self.assertEqual(premiere["venue"], "Contra-Kreis-Theater")
        self.assertIn("Am Hof 3-5", premiere["venue_address"])
        self.assertEqual(
            premiere["link"], "https://www.contra-kreis-theater.de/theater-archiv/rita-wills-wissen/"
        )
        self.assertEqual(premiere["category_key"], "stage")
        self.assertEqual(premiere["source_id"], "contra-kreis-theater")

    def test_source_is_registered_with_stable_id(self):
        self.assertIs(SOURCES["Contra-Kreis-Theater"], contra_kreis.fetch)
        self.assertEqual(SOURCE_IDS["Contra-Kreis-Theater"], "contra-kreis-theater")


if __name__ == "__main__":
    unittest.main()
