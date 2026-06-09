import unittest
from datetime import datetime

from scripts.nrw_events import common
from scripts.nrw_events.sources import SOURCES


class SourceParserTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 6, 9)
        common.END_DATE = datetime(2026, 6, 21)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

    def test_ecmaps_tiles_create_events_from_dated_destination_one_cards(self):
        html = """
        <div class="tile tile--one-quarter tile--single-height">
          <a href="/event/street-food-festival-eitorf" class="tile__link">
            <div class="tile__addon">
              <span class="tile__label-text tile__addon-icon-label">13.06.2026</span>
            </div>
            <p class="typo-m header__line header__head"> Street Food Festival Eitorf </p>
            <span class="icontext__text">Sekundarschule Eitorf, Eitorf</span>
          </a>
        </div>
        """

        events = common.events_from_ecmaps_tiles(
            html, "Naturregion Sieg", "Naturregion Sieg",
            "natur outdoor markt kultur", 0.9, "https://naturregion-sieg.de")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Street Food Festival Eitorf")
        self.assertEqual(events[0]["date"], "2026-06-13")
        self.assertEqual(events[0]["venue"], "Sekundarschule Eitorf, Eitorf")
        self.assertEqual(events[0]["city"], "Eitorf")
        self.assertEqual(events[0]["link"], "https://naturregion-sieg.de/event/street-food-festival-eitorf")

    def test_wp_event_manager_listing_uses_location_city_and_event_time(self):
        html = """
        <div class="event_listing">
          <a href="https://www.ruhr-guide.de/veranstaltung/open-air-am-rhein/" class="wpem-event-action-url">
            <div class="wpem-event-title">
              <h3 class="wpem-heading-text">Open Air am Rhein</h3>
            </div>
            <div class="wpem-event-date-time">
              <span class="wpem-event-date-time-text">
                17.06.2026 @ 19:00 - 17.06.2026 @ 22:30
              </span>
            </div>
            <div class="wpem-event-location">
              <span class="wpem-event-location-text">Düsseldorf, Rheinufer</span>
            </div>
          </a>
        </div>
        """

        events = common.events_from_wp_event_manager_listing(
            html, "Ruhr-Guide", "events ruhrgebiet nrw konzert kultur", 0.65)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Open Air am Rhein")
        self.assertEqual(events[0]["date"], "2026-06-17")
        self.assertEqual(events[0]["time"], "19:00-22:30")
        self.assertEqual(events[0]["city"], "Düsseldorf")

    def test_requested_sources_are_registered(self):
        self.assertIn("Naturregion Sieg", SOURCES)
        self.assertIn("Troisdorf", SOURCES)
        self.assertIn("Ruhr-Guide", SOURCES)


if __name__ == "__main__":
    unittest.main()
