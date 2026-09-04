import unittest
from datetime import datetime

from nrw_events import report
from nrw_events.sources import ssf_bonn

from tests.helpers import patch_window


class SsFBonnTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 9, 1), datetime(2026, 9, 30))

    def test_first_party_festival_page_emits_current_occurrence(self):
        html = """
        <div class="headline2">49. SSF Festival</div>
        <div class="text-wrapper">
          <p><span class="headline3">am 05.09.2026 auf dem Münsterplatz</span></p>
          <p>Das traditionelle SSF Festival findet in diesem Jahr am Samstag,
             5. September statt. Wie gewohnt präsentieren sich die SSF Bonn.</p>
          <p>Von 12:00 Uhr bis 18:00 Uhr erwarten die Besucher Vorführungen,
             Mitmachaktionen, Kinderschminken, Glücksrad und Hüpfburg.</p>
        </div>
        <div class="headline2">48. SSF Festival</div>
        <div class="text-wrapper">
          <p><span class="headline3">am 06.09.2025 auf dem Münsterplatz</span></p>
        </div>
        """

        events = ssf_bonn.events_from_page(html)

        current = [event for event in events if event["date"] == "2026-09-05"]
        self.assertEqual(len(current), 1)
        event = current[0]
        self.assertEqual(event["title"], "SSF Festival")
        self.assertEqual(event["date"], "2026-09-05")
        self.assertEqual(event["time"], "12:00–18:00")
        self.assertEqual(event["venue"], "Münsterplatz")
        self.assertEqual(event["city"], "Bonn")
        self.assertEqual(event["source"], "SSF Bonn")
        self.assertEqual(
            event["link"],
            "https://www.ssfbonn.de/de/aktuelles/veranstaltungen/ssf-festival/",
        )
        self.assertIn("Mitmachaktionen", event["description"])

    def test_parser_deduplicates_mobile_and_desktop_copies(self):
        block = """
        <div class="headline2">49. SSF Festival</div>
        <div class="text-wrapper">
          <p><span class="headline3">am 05.09.2026 auf dem Münsterplatz</span></p>
          <p>Von 12:00 Uhr bis 18:00 Uhr gibt es Sport und Musik.</p>
        </div>
        """

        events = ssf_bonn.events_from_page(block + block)

        self.assertEqual(len(events), 1)

    def test_first_party_record_replaces_press_release_duplicate(self):
        html = """
        <div class="headline2">49. SSF Festival</div>
        <div class="text-wrapper">
          <p><span class="headline3">am 05.09.2026 auf dem Münsterplatz</span></p>
          <p>Von 12:00 Uhr bis 18:00 Uhr gibt es Sport und Musik.</p>
        </div>
        """
        [primary] = ssf_bonn.events_from_page(html)
        press = {
            **primary,
            "title": "SSF-Festival",
            "source": "Bonn district festivals",
            "source_id": "bonn-district-festivals",
            "link": (
                "https://www.bonn.de/pressemitteilungen/dezember/"
                "abwechslungsreiches-veranstaltungsjahr-2026-in-bonn.php"
            ),
            "score": 0.72,
        }

        deduped = report.deduplicate([press, primary])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "SSF Bonn")
        self.assertEqual(deduped[0]["link"], ssf_bonn.URL)
