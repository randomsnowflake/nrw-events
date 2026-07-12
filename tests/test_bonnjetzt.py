import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.nrw_events.sources import SOURCES, bonnjetzt
from scripts.nrw_events.validation import validate_event


class BonnJetztTests(unittest.TestCase):
    def test_ongoing_multiday_event_has_canonical_dates(self):
        html = """
<article itemtype="https://schema.org/Event">
  <a href="/event/offene-gartenpforte" itemprop="url">
    <h2 class="title p-name">Offene Gartenpforte Rheinland</h2>
  </a>
  <time datetime="2026-07-11T10:00:00" itemprop="startDate">Samstag, 11. Juli, 10:00</time>
  <time itemprop="endDate" content="2026-07-12T18:00:00"></time>
  <span itemprop="name">Haus der Geschichte Bonn</span>
  <div itemprop="address">Bonn</div>
  <span class="v-chip__content">Garten</span>
</article>
"""
        with (
            patch("scripts.nrw_events.common.fetch_url", return_value=html),
            patch("scripts.nrw_events.common.TODAY", datetime(2026, 7, 12)),
            patch("scripts.nrw_events.common.END_DATE", datetime(2026, 7, 25)),
        ):
            events = bonnjetzt.fetch()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_date"], "2026-07-11")
        self.assertEqual(events[0]["end_date"], "2026-07-12")
        self.assertEqual(validate_event(events[0])["start_date"], "2026-07-11")

    def test_eventbrite_is_not_registered(self):
        self.assertNotIn("Eventbrite Party", SOURCES)


if __name__ == "__main__":
    unittest.main()
