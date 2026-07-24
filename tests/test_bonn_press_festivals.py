import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import bonn
from tests.helpers import patch_window


class BonnPressFestivalTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 8, 31))

    def test_keeps_comma_inside_hyphenated_official_market_name(self):
        html = """
        <ul>
          <li>
            Antik-, Kunst- &amp; Designmarkt Bonn, Friedensplatz,
            Bottlerplatz, Vivatsgasse, Poststraße, 16. August 2026, Rhein-Antik
          </li>
        </ul>
        """

        with patch.object(common, "fetch_url", return_value=html):
            events = bonn.fetch_press_festivals()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Antik-, Kunst- & Designmarkt Bonn")
        self.assertEqual(events[0]["date"], "2026-08-16")
        self.assertEqual(events[0]["category_key"], "market")

    def test_parses_shared_month_ranges_as_one_multi_day_event(self):
        self.addCleanup(patch.stopall)
        patch.object(common, "TODAY", datetime(2026, 11, 1)).start()
        patch.object(common, "END_DATE", datetime(2026, 12, 31)).start()
        html = """
        <ul>
          <li>Nikolausmarkt Beuel, Hermannstraße, 27. bis 29. November 2026, Bundesstadt Bonn</li>
          <li>Kessenicher Herbstmarkt, Pützstraße, 3. und 4. Oktober 2026, Ortsausschuss</li>
        </ul>
        """

        with patch.object(common, "fetch_url", return_value=html):
            events = bonn.fetch_press_festivals()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Nikolausmarkt Beuel")
        self.assertEqual(events[0]["start_date"], "2026-11-27")
        self.assertEqual(events[0]["end_date"], "2026-11-29")
        self.assertEqual(events[0]["date"], "2026-11-27–2026-11-29")
        self.assertEqual(events[0]["venue"], "Hermannstraße")
        self.assertEqual(events[0]["category_key"], "market")


if __name__ == "__main__":
    unittest.main()
