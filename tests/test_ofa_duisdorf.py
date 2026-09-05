import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import bonn, ofa_duisdorf

from tests.helpers import patch_window

PAGE_HTML = """
<h2>Duisdorfer Adventsmarkt 2024 vom 29.11.2024 bis 01.12.2024!</h2>
<h2>Duisdorfer Adventsmarkt 2026 vom 27.11.2026 bis 29.11.2026!</h2>
<p>Am 1. Adventswochende richtet der OFA-Duisdorf den Duisdorfer Adventsmarkt aus.</p>
<p>Der Adventsmarkt ist ein kleiner, aber feiner ehrenamtlich organisierter
Adventsmarkt mit Beteiligung von diversen ortsansässigen Vereinen,
Organisationen und Gewerbetreibenden.</p>
"""


class OfaDuisdorfTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 11, 1), datetime(2026, 12, 10))

    def test_first_party_page_emits_latest_exact_range(self):
        [event] = ofa_duisdorf.events_from_page(PAGE_HTML)

        self.assertEqual(event["title"], "Duisdorfer Adventsmarkt")
        self.assertEqual(event["start_date"], "2026-11-27")
        self.assertEqual(event["end_date"], "2026-11-29")
        self.assertTrue(event["all_day"])
        self.assertEqual(event["venue"], "")
        self.assertEqual(event["city"], "Bonn-Duisdorf")
        self.assertEqual(event["source_id"], "ofa-duisdorf")
        self.assertEqual(event["category_key"], "market")
        self.assertIn("ehrenamtlich organisierter Adventsmarkt", event["description"])
        self.assertIn("ortsansässigen Vereinen", event["description"])

    def test_conflicting_current_ranges_are_not_guessed(self):
        html = PAGE_HTML + (
            "<h2>Duisdorfer Adventsmarkt 2026 vom 28.11.2026 bis 30.11.2026!</h2>"
        )

        self.assertEqual(ofa_duisdorf.events_from_page(html), [])

    def test_identical_responsive_copies_emit_one_event(self):
        heading = "Duisdorfer Adventsmarkt 2026 vom 27.11.2026 bis 29.11.2026!"
        html = PAGE_HTML.replace(heading, f"{heading}{heading}")

        [event] = ofa_duisdorf.events_from_page(html)

        self.assertEqual(event["start_date"], "2026-11-27")
        self.assertEqual(event["end_date"], "2026-11-29")

    def test_implausibly_long_market_range_is_rejected(self):
        html = PAGE_HTML.replace(
            "27.11.2026 bis 29.11.2026",
            "01.01.2026 bis 31.12.2026",
        )

        self.assertEqual(ofa_duisdorf.events_from_page(html), [])

    def test_wrong_advent_weekend_is_rejected(self):
        html = PAGE_HTML.replace(
            "27.11.2026 bis 29.11.2026",
            "13.11.2026 bis 15.11.2026",
        )

        self.assertEqual(ofa_duisdorf.events_from_page(html), [])

    def test_minor_description_edit_keeps_the_identified_event(self):
        html = PAGE_HTML.replace(
            "richtet der OFA-Duisdorf den Duisdorfer Adventsmarkt aus",
            "veranstaltet der OFA-Duisdorf den Duisdorfer Adventsmarkt",
        )

        [event] = ofa_duisdorf.events_from_page(html)

        self.assertIn("ehrenamtlich organisierter Adventsmarkt", event["description"])

    def test_page_without_description_emits_core_event(self):
        html = PAGE_HTML.replace(
            "<p>Am 1. Adventswochende richtet der OFA-Duisdorf den Duisdorfer "
            "Adventsmarkt aus.</p>",
            "",
        ).replace(
            "<p>Der Adventsmarkt ist ein kleiner, aber feiner ehrenamtlich organisierter\n"
            "Adventsmarkt mit Beteiligung von diversen ortsansässigen Vereinen,\n"
            "Organisationen und Gewerbetreibenden.</p>",
            "",
        )

        [event] = ofa_duisdorf.events_from_page(html)

        self.assertEqual(event["description"], "")

    def test_first_party_occurrence_replaces_press_release_record(self):
        [primary] = ofa_duisdorf.events_from_page(PAGE_HTML)
        press_html = """
        <ul><li>
          Duisdorfer Adventsmarkt, Duisdorf,
          27. bis 29. November 2026, Ortsfestausschuss Duisdorf
        </li></ul>
        """
        with patch.object(common, "fetch_url", return_value=press_html):
            [press] = bonn.fetch_press_festivals()

        deduped = report.deduplicate([press, primary])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source_id"], "ofa-duisdorf")
        self.assertIn("ehrenamtlich", deduped[0]["description"])

    def test_fetch_uses_the_first_party_page(self):
        with patch.object(common, "fetch_url", return_value=PAGE_HTML):
            [event] = ofa_duisdorf.fetch()

        self.assertEqual(event["source_id"], "ofa-duisdorf")
        self.assertEqual(event["link"], ofa_duisdorf.URL)


if __name__ == "__main__":
    unittest.main()
