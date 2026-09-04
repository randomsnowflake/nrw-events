import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import bonn, lengsdorfer_weinfest

from tests.helpers import patch_window

PAGE_HTML = """
<h1>29. Lengsdorfer Weinfest 2026 | Wein &amp; Tradition in Bonn</h1>
<script>
const weinfestDaten = [
  { tag: 'Freitag', zeit: '17:00', titel: 'Öffnung der Weinbrunnen', text: 'Live-Musik' },
  { tag: 'Freitag', zeit: '19:00', titel: 'Krönung der Weinkönigin', text: 'Krönung von ALESSA I.' },
  { tag: 'Samstag', zeit: '16:00', titel: 'Öffnung Weinbrunnen', text: 'Platzkonzert' },
  { tag: 'Sonntag', zeit: '11:00', titel: 'Frühschoppen', text: 'Musikalischer Start' },
  { tag: 'Sonntag', zeit: '22:00', titel: 'Ausklang', text: 'Offizielles Ende' }
];
</script>
<p>18-20. September 2026<br>Lengsdorfer Dorfplatz<br>53127 Bonn</p>
"""


class LengsdorferWeinfestTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 9, 1), datetime(2026, 9, 30))

    def test_first_party_page_emits_exact_range_and_programme_times(self):
        [event] = lengsdorfer_weinfest.events_from_page(PAGE_HTML)

        self.assertEqual(event["title"], "Weinfest Lengsdorf")
        self.assertEqual(event["start_date"], "2026-09-18")
        self.assertEqual(event["end_date"], "2026-09-20")
        self.assertEqual(event["start_at"], "2026-09-18T17:00+02:00")
        self.assertEqual(event["end_at"], "2026-09-20T22:00+02:00")
        self.assertEqual(event["venue"], "Lengsdorfer Dorfplatz")
        self.assertEqual(event["city"], "Bonn")
        self.assertEqual(event["source_id"], "lengsdorfer-weinfest")
        self.assertEqual(event["description_source"], "generated")
        self.assertIn("Krönung der Weinkönigin", event["description"])
        self.assertIn("Samstag beginnt das Programm um 16:00", event["description"])

    def test_first_party_occurrence_replaces_press_release_record(self):
        [primary] = lengsdorfer_weinfest.events_from_page(PAGE_HTML)
        press_html = """
        <ul><li>
          Weinfest Lengsdorf, Lengsdorfer Dorfplatz,
          18. bis 20. September 2026, Weinfestausschuss Lengsdorf
        </li></ul>
        """
        with patch.object(common, "fetch_url", return_value=press_html):
            [press] = bonn.fetch_press_festivals()

        deduped = report.deduplicate([press, primary])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source_id"], "lengsdorfer-weinfest")
        self.assertEqual(deduped[0]["start_at"], "2026-09-18T17:00+02:00")

    def test_historical_footer_does_not_replace_current_footer_dates(self):
        html = (
            "<p>19-21. September 2025<br>Archivplatz<br>53127 Bonn</p>"
            + PAGE_HTML
        )

        [event] = lengsdorfer_weinfest.events_from_page(html)

        self.assertEqual(event["start_date"], "2026-09-18")
        self.assertEqual(event["end_date"], "2026-09-20")
        self.assertEqual(event["venue"], "Lengsdorfer Dorfplatz")

    def test_location_is_scraped_instead_of_hard_coded(self):
        html = PAGE_HTML.replace("Lengsdorfer Dorfplatz", "Lengsdorfer Festwiese")

        [event] = lengsdorfer_weinfest.events_from_page(html)

        self.assertEqual(event["venue"], "Lengsdorfer Festwiese")

    def test_unpadded_clock_is_sorted_numerically(self):
        html = PAGE_HTML.replace("zeit: '17:00'", "zeit: '9:00'")

        [event] = lengsdorfer_weinfest.events_from_page(html)

        self.assertEqual(event["start_at"], "2026-09-18T09:00+02:00")

    def test_invalid_clock_rejects_the_page(self):
        invalid_pages = (
            PAGE_HTML.replace("zeit: '17:00'", "zeit: '24:00'"),
            PAGE_HTML.replace("zeit: '22:00'", "zeit: '9:60'"),
        )

        for html in invalid_pages:
            with self.subTest(html=html):
                self.assertEqual(lengsdorfer_weinfest.events_from_page(html), [])

    def test_page_without_location_is_not_guessed(self):
        html = PAGE_HTML.replace(
            "<br>Lengsdorfer Dorfplatz<br>53127 Bonn",
            "",
        )

        self.assertEqual(lengsdorfer_weinfest.events_from_page(html), [])

    def test_page_without_programme_is_not_guessed(self):
        html = PAGE_HTML.replace("const weinfestDaten", "const archivedData")

        self.assertEqual(lengsdorfer_weinfest.events_from_page(html), [])


if __name__ == "__main__":
    unittest.main()
