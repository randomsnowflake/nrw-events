import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import koenigswinter
from tests.helpers import patch_window


class KoenigswinterParserTests(unittest.TestCase):
    def setUp(self):
        self.cache_env = patch.dict(
            "os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"})
        self.cache_env.start()
        common._reset_detail_page_cache()
        patch_window(self, datetime(2026, 7, 13), datetime(2026, 7, 26))

    def tearDown(self):
        common._reset_detail_page_cache()
        self.cache_env.stop()

    def test_fetch_enriches_listing_card_with_detail_page_copy(self):
        listing_html = """
<span class="text-muted">Sport, Freizeit, Gesundheit</span>
<h4>
  <a href="/de/veranstaltungskalender/event/132010,1081/radfernfahrt-koenigswinter--cognac-2026-13-07-2026.html">Radfernfahrt Königswinter – Cognac 2026</a>
</h4>
<div class="mb-2">
  <i class="icon icon-calendar-day"></i> 13.07.2026 - 23.07.2026 von 08:00 Uhr bis 13:00 Uhr
</div>
<div class="location mt-2">
  <span class="gcevent-list-location-span">Rheinfähre Königswinter/Bonn-Mehlem</span>
</div>
"""
        detail_html = """
<div class="event-content mb-2">
  <p>Der Radtreff Campus Bonn plant eine Fahrradfernfahrt von Königswinter nach Cognac.<br />
  Die Strecke wird als Brevet und in mehreren touristischen Etappen angeboten.</p>
</div>
"""

        def fake_fetch(url, *args, **kwargs):
            if url.endswith("veranstaltungskalender.html"):
                return listing_html
            if "/veranstaltungskalender/event/" in url:
                return detail_html
            raise AssertionError(f"unexpected URL: {url}")

        with patch("nrw_events.common.fetch_url", side_effect=fake_fetch):
            events = koenigswinter.fetch()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Radfernfahrt Königswinter – Cognac 2026")
        self.assertEqual(event["time"], "08:00 bis 13:00")
        self.assertEqual(event["venue"], "Rheinfähre Königswinter/Bonn-Mehlem")
        self.assertEqual(event["category_key"], "sports")
        self.assertIn("Fahrradfernfahrt von Königswinter nach Cognac", event["description"])
        self.assertIn("mehreren touristischen Etappen", event["description"])

    def test_listing_parser_builds_sentence_when_detail_copy_is_missing(self):
        listing_html = """
<span class="text-muted">Musik</span>
<h4><a href="/de/veranstaltungskalender/event/140531,1081/konzert-14-07-2026.html">Konzert der Sandbach School</a></h4>
<div class="mb-2"><i class="icon icon-calendar-day"></i> 14.07.2026 von 16:30 Uhr bis 17:30 Uhr</div>
<span class="gcevent-list-location-span">Marktplatz Königswinter-Altstadt</span>
"""

        [event] = koenigswinter._events_from_listing(
            listing_html,
            detail_fetcher=lambda _url: '<div class="event-content mb-2"></div>',
        )

        self.assertIn("Konzert der Sandbach School", event["description"])
        self.assertIn("16:30 bis 17:30 Uhr", event["description"])
        self.assertIn("Marktplatz Königswinter-Altstadt", event["description"])
        self.assertTrue(event["description"].endswith("."))

    def test_cards_do_not_borrow_following_schedule_or_venue(self):
        listing_html = """
<li class="media mb-3 oddeven0 subkey-public-90">
  <div class="media-body">
    <span class="text-muted">Sport, Freizeit, Gesundheit</span>
    <h4><a href="/de/veranstaltungskalender/event/140379,1081/kubb-18-07-2026.html">2. Benefiz-Kubbturnier</a></h4>
    <div class="mb-2"><i class="icon icon-calendar-day"></i> 18.07.2026 - 19.07.2026 ab 12:00 Uhr</div>
    <span class="gcevent-list-location-span">Rheinwiese Niederdollendorf / Rheinufer</span>
  </div>
</li>
<li class="media mb-3 oddeven1 subkey-public-90">
  <div class="media-body">
    <span class="text-muted">Volksfeste, Märkte</span>
    <h4><a href="/de/veranstaltungskalender/event/140689,1081/weinfest-24-07-2026.html">Oberpleiser Weinfest</a></h4>
    <div class="mb-2"><i class="icon icon-calendar-day"></i> 24.07.2026 - 26.07.2026 von 17:00 Uhr bis 18:00 Uhr</div>
    <span class="gcevent-list-location-span">Rathausvorplatz Oberpleiser</span>
  </div>
</li>
"""

        events = koenigswinter._events_from_listing(
            listing_html,
            detail_fetcher=lambda _url: "",
        )

        self.assertEqual(len(events), 2)
        kubb, weinfest = events
        self.assertEqual(kubb["start_date"], "2026-07-18")
        self.assertEqual(kubb["time"], "12:00")
        self.assertEqual(kubb["venue"], "Rheinwiese Niederdollendorf / Rheinufer")
        self.assertEqual(weinfest["start_date"], "2026-07-24")
        self.assertEqual(weinfest["time"], "17:00 bis 18:00")
        self.assertEqual(weinfest["venue"], "Rathausvorplatz Oberpleiser")


if __name__ == "__main__":
    unittest.main()
