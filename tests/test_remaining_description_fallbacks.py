import unittest
from datetime import timedelta

from nrw_events import common
from nrw_events.sources import bonn


class RemainingDescriptionFallbackTests(unittest.TestCase):
    def test_bonnfest_sports_rows_collapse_to_the_reviewed_three_day_range(self):
        html = """
<article data-source="sports" class="SP-Teaser SP-Teaser--textual">
  <a href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/BonnFest-2026.php" class="SP-Teaser__inner">
    <span class="SP-Kicker__text">Stadtfest</span>
    <span class="SP-Scheduling__date">25.09.2026</span>
    <span class="SP-Scheduling__date">26.09.2026</span>
    <span class="SP-Scheduling__date">27.09.2026</span>
    <h1 class="SP-Teaser__headline">BonnFest 2026</h1>
  </a>
</article>
"""

        raw_events = bonn.events_from_sport_teasers(html)
        raw_events[0].update({
            "time": "12:00",
            "start_at": "2026-09-25T12:00+02:00",
            "end_at": "2026-09-25T18:00+02:00",
            "all_day": False,
            "description_html": "<p>BonnFest nur am 25. September 2026.</p>",
        })
        events = bonn._apply_reviewed_sport_occurrence_corrections(raw_events)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_date"], "2026-09-25")
        self.assertEqual(events[0]["end_date"], "2026-09-27")
        self.assertEqual(events[0]["time"], "")
        self.assertEqual(events[0]["start_at"], "")
        self.assertEqual(events[0]["end_at"], "")
        self.assertTrue(events[0]["all_day"])
        self.assertEqual(events[0]["description_html"], "")
        self.assertEqual(events[0]["description_source"], "generated")
        self.assertIn("25. bis 27. September 2026", events[0]["description"])

    def test_bonn_sports_listing_has_factual_description(self):
        event_date = (common.TODAY + timedelta(days=1)).strftime("%d.%m.%Y")
        html = f"""
<article data-source="sports" class="SP-Teaser SP-Teaser--textual">
  <a href="/veranstaltungen/sporttag.php" rel="bookmark" class="SP-Teaser__inner">
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
