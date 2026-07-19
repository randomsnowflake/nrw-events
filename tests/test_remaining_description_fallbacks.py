import unittest
from datetime import timedelta

from nrw_events import common
from nrw_events.sources import bonn


class RemainingDescriptionFallbackTests(unittest.TestCase):
    def test_bonn_sports_listing_has_factual_description(self):
        event_date = (common.TODAY + timedelta(days=1)).strftime("%d.%m.%Y")
        html = f"""
<article class="SP-Teaser SP-Teaser--textual">
  <a class="SP-Teaser__inner" href="/veranstaltungen/sporttag.php">
    <span class="SP-Kicker__text">Sport</span>
    <span class="SP-Scheduling__date">{event_date}</span>
    <span class="SP-Scheduling__time">18:30 Uhr</span>
    <h1 class="SP-Teaser__headline">Offener Sporttag</h1>
  </a>
</article>
"""

        events = bonn.events_from_sport_teasers(html)

        self.assertEqual(len(events), 1)
        self.assertIn("Offener Sporttag", events[0]["description"])
        self.assertIn(event_date, events[0]["description"])
        self.assertIn("18:30 Uhr", events[0]["description"])

if __name__ == "__main__":
    unittest.main()
