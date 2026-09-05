import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import bonn, kessenicher_herbstmarkt

from tests.helpers import patch_window

PAGE_HTML = """
<h1>Informationen Kessenicher Herbstmarkt</h1>
<p>Tag: 04 .10.2026</p>
<p>Öffnungszeiten: Herbstmarkt: 11.00 Uhr bis 18.00 Uhr</p>
<p>Standort Herbstmarkt: Pützstraße • Burbacher Straße • Rheinweg</p>
<p>Bühne: auf der Pützstraße an der Kirchwiese</p>
<p>Bühnenprogramm: folgt</p>
<p>Die Informationen zum Kinderflohmarkt/Erwachsenenflohmarkt finden Sie hier.</p>
"""

HOME_HTML = """
<p>Am Samstag, den 26.09.2026, abends ab 20.00 Uhr, findet ein Helfertreffen statt.</p>
<p>Nach einem Jahr Pause ist die Organisation für den Kessenicher Herbstmarkt
wieder in vollem Gange. Am Samstag, den 03.10.2026, abends ab 18.00 Uhr, geht es
wieder mit dem Herbstmarkt Opening los. Am Sonntag, den 04.10.2026, kann man es
sich dann ab 11.00 Uhr auf dem Kessenicher Herbstmarkt gut gehen lassen.</p>
"""


class KessenicherHerbstmarktTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 1), datetime(2026, 10, 31))

    def test_first_party_page_emits_exact_market_schedule(self):
        [event] = kessenicher_herbstmarkt.events_from_page(PAGE_HTML)

        self.assertEqual(event["title"], "Kessenicher Herbstmarkt")
        self.assertEqual(event["date"], "2026-10-04")
        self.assertEqual(event["time"], "11:00–18:00")
        self.assertEqual(event["venue"], "Pützstraße / Burbacher Straße / Rheinweg")
        self.assertEqual(event["venue_address"], "")
        self.assertEqual(event["city"], "Bonn")
        self.assertEqual(event["location_confidence"], "known_city")
        self.assertEqual(event["source"], "Kessenicher Herbstmarkt")
        self.assertEqual(event["source_id"], "kessenicher-herbstmarkt")
        self.assertEqual(event["category_key"], "market")
        self.assertEqual(event["description_source"], "generated")
        self.assertIn("Kirchwiese", event["description"])

    def test_first_party_home_page_emits_opening_as_separate_event(self):
        [event] = kessenicher_herbstmarkt.opening_events_from_page(HOME_HTML)

        self.assertEqual(event["title"], "Herbstmarkt Opening")
        self.assertEqual(event["date"], "2026-10-03")
        self.assertEqual(event["time"], "18:00")
        self.assertEqual(event["start_at"], "2026-10-03T18:00+02:00")
        self.assertEqual(event["source_id"], "kessenicher-herbstmarkt")

    def test_first_party_occurrence_replaces_press_release_range(self):
        [primary] = kessenicher_herbstmarkt.events_from_page(PAGE_HTML)
        press_html = """
        <ul><li>
          Kessenicher Herbstmarkt, Pützstraße,
          3. und 4. Oktober 2026, Ortsausschuss
        </li></ul>
        """
        with patch.object(common, "fetch_url", return_value=press_html):
            [press] = bonn.fetch_press_festivals()

        self.assertTrue(press["all_day"])
        self.assertEqual(press["start_at"], "")
        self.assertEqual(press["end_at"], "")
        self.assertEqual(press["source"], "Bonn district festivals")

        deduped = report.deduplicate([press, primary])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Kessenicher Herbstmarkt")
        self.assertEqual(deduped[0]["date"], "2026-10-04")

    def test_page_without_exact_date_is_not_guessed(self):
        html = PAGE_HTML.replace("Tag: 04 .10.2026", "Termin folgt")

        self.assertEqual(kessenicher_herbstmarkt.events_from_page(html), [])

    def test_page_without_market_hours_is_not_guessed(self):
        html = PAGE_HTML.replace(
            "Öffnungszeiten: Herbstmarkt: 11.00 Uhr bis 18.00 Uhr",
            "Öffnungszeiten folgen",
        )

        self.assertEqual(kessenicher_herbstmarkt.events_from_page(html), [])

    def test_page_without_market_location_is_not_guessed(self):
        html = PAGE_HTML.replace(
            "Standort Herbstmarkt: Pützstraße • Burbacher Straße • Rheinweg",
            "Standort folgt",
        )

        self.assertEqual(kessenicher_herbstmarkt.events_from_page(html), [])
