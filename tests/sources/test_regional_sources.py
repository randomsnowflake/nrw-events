import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.nrw_events import common
from scripts.nrw_events.sources import naturregion_sieg, regional_venues

from .parser_cases import case_class

BONN_MARKERS = (
    "bonn", "brotfabrik", "pantheon", "kult41", "kunstmuseum",
    "bundeskunsthalle", "botanical", "springmaus", "repair_cafes",
    "brueckenforum", "vox_bona",
)
PIPELINE_MARKERS = ("make_event", "search_fallback", "date_for_window", "ical_")


RegionalSourceTests = case_class(
    "RegionalSourceTests",
    lambda name: not any(marker in name for marker in BONN_MARKERS + PIPELINE_MARKERS),
)


class RheinbachParserTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        self.cache_env = patch.dict(
            "os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"})
        self.cache_env.start()
        common._reset_detail_page_cache()
        common.TODAY = datetime(2026, 7, 13)
        common.END_DATE = datetime(2026, 7, 26)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date
        common._reset_detail_page_cache()
        self.cache_env.stop()

    def test_rheinbach_uses_detail_copy_and_structured_listing_fields(self):
        listing_html = """
<div class="row event-item mb-4">
  <div class="categories">
    <span>Aktiv</span>, <span>Rheinbach</span>
  </div>
  <p class="date">Dienstag, 14.07.2026</p>
  <a href="/veranstaltungen/veranstaltung/wald-und-kaffee--598">
    <h2 class="h3 title">Wald und Kaffee</h2>
  </a>
  <div class="time"><span>14:00 Uhr</span></div>
  <p class="location">Eifelhaus, Neukirchener Weg 11, Rheinbach</p>
</div>
<button class="event-more-button">mehr</button>
"""
        detail_html = """
<div class="event-detail">
  <div class="teaser"><b><p>&nbsp;</p></b></div>
  <div class="bodytext">
    <p>Kleine Wanderung im Rheinbacher Wald und anschließend Kaffee trinken im Eifelhaus</p>
  </div>
  <div class="additional_info"><p>Eifel- und Heimatverein Rheinbach e.V.</p></div>
</div>
"""

        events = regional_venues._events_from_rheinbach(
            listing_html,
            detail_fetcher=lambda _url: detail_html,
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Wald und Kaffee")
        self.assertEqual(event["time"], "14:00")
        self.assertEqual(event["venue"], "Eifelhaus, Neukirchener Weg 11, Rheinbach")
        self.assertEqual(event["category_key"], "outdoor")
        self.assertIn("Kleine Wanderung im Rheinbacher Wald", event["description"])
        self.assertTrue(event["description"].endswith("."))
        self.assertNotIn("14 | 07 Aktiv", event["description"])

    def test_rheinbach_builds_a_complete_fallback_description(self):
        listing_html = """
<div class="row event-item mb-4">
  <div class="categories">
    <span>Allgemein</span>, <span>Sport</span>, <span>Aktiv</span>, <span>Rheinbach</span>
  </div>
  <p class="date">Montag, 13.07.2026</p>
  <a href="/veranstaltungen/veranstaltung/rckengymnastik-596">
    <h2 class="h3 title">Rückengymnastik</h2>
  </a>
  <div class="time"><span>18:00 - 19:00 Uhr</span></div>
  <p class="location">Freizeitpark Rheinbach</p>
</div>
<button class="event-more-button">mehr</button>
"""
        empty_detail_html = """
<div class="event-detail">
  <div class="teaser"><p>&nbsp;</p></div>
  <div class="bodytext"><p>&nbsp;</p></div>
</div>
"""

        [event] = regional_venues._events_from_rheinbach(
            listing_html,
            detail_fetcher=lambda _url: empty_detail_html,
        )

        self.assertEqual(event["title"], "Rückengymnastik")
        self.assertIn("Sport- und Aktivangebot", event["description"])
        self.assertIn("18:00 bis 19:00 Uhr", event["description"])
        self.assertIn("Freizeitpark Rheinbach", event["description"])
        self.assertTrue(event["description"].endswith("."))


class NaturregionSiegParserTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        self.cache_env = patch.dict(
            "os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"})
        self.cache_env.start()
        common._reset_detail_page_cache()
        common.TODAY = datetime(2026, 7, 13)
        common.END_DATE = datetime(2026, 7, 26)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date
        common._reset_detail_page_cache()
        self.cache_env.stop()

    def test_fetch_enriches_listing_event_from_detail_jsonld(self):
        listing_html = """
<div class="tile tile--one-quarter tile--single-height">
  <a href="/event/besucherfuehrung-grube-silberhardt" class="tile__link">
    <span class="tile__label-text">14.07.2026</span>
    <p class="header__head">Besucherführung Grube Silberhardt</p>
    <span class="icontext__text">Besucherbergwerk Grube Silberhardt, Windeck</span>
  </a>
</div>
"""
        detail_html = """
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": ["Event", "WebPage"],
  "name": "Besucherführung Grube Silberhardt",
  "description": "<p>Die Führung zeigt den historischen Erzabbau im Besucherbergwerk.</p>",
  "startDate": "2026-07-14T11:30:00+02:00",
  "endDate": "2026-07-14T13:00:00+02:00",
  "url": "https://naturregion-sieg.de/event/besucherfuehrung-grube-silberhardt",
  "location": {
    "@type": "Place",
    "name": "Besucherbergwerk Grube Silberhardt",
    "address": {"@type": "PostalAddress", "addressLocality": "Windeck"}
  }
}
</script>
"""

        def fake_fetch(url, *args, **kwargs):
            if url == naturregion_sieg._URL:
                return listing_html
            self.assertEqual(
                url,
                "https://naturregion-sieg.de/event/besucherfuehrung-grube-silberhardt",
            )
            return detail_html

        with patch.object(common, "fetch_url", side_effect=fake_fetch):
            [event] = naturregion_sieg.fetch()

        self.assertEqual(event["description"], "Die Führung zeigt den historischen Erzabbau im Besucherbergwerk.")
        self.assertEqual(event["time"], "11:30–13:00")
        self.assertEqual(event["venue"], "Besucherbergwerk Grube Silberhardt")
        self.assertEqual(event["city"], "Windeck")
        self.assertEqual(event["category_key"], "outdoor")
        self.assertFalse(event["all_day"])

    def test_fetch_keeps_rich_copy_for_a_trusted_recurring_outdoor_event(self):
        listing_html = """
<div class="tile tile--one-quarter tile--single-height">
  <a href="/event/kraeuterwanderung-kleine-naturzeit-am-kloster-marienthal" class="tile__link">
    <span class="tile__label-text">16.07.2026</span>
    <p class="header__head">Kräuterwanderung &quot;Kleine Naturzeit&quot; Am Kloster Marienthal</p>
    <span class="icontext__text">Wildwuchs-Windeck Sandra Häfner, Windeck</span>
  </a>
</div>
"""
        detail_html = """
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": ["Event", "WebPage"],
  "name": "Kräuterwanderung \\\"Kleine Naturzeit\\\" Am Kloster Marienthal",
  "description": "<p>Regelmäßige Streifzüge durch die Jahreszeiten: Wir entdecken Wildkräuter, Vögel und Tiere.</p>",
  "startDate": "2026-07-16T16:30:00+02:00",
  "endDate": "2026-07-16T18:30:00+02:00",
  "url": "https://naturregion-sieg.de/event/kraeuterwanderung-kleine-naturzeit-am-kloster-marienthal"
}
</script>
"""

        with patch.object(common, "fetch_url", side_effect=[listing_html, detail_html]):
            [event] = naturregion_sieg.fetch()

        self.assertIn("Wildkräuter, Vögel und Tiere", event["description"])
        self.assertNotIn("Weitere Informationen stehen", event["description"])
        self.assertEqual(event["time"], "16:30–18:30")
        self.assertEqual(event["start_at"], "2026-07-16T16:30+02:00")
        self.assertEqual(event["end_at"], "2026-07-16T18:30+02:00")
        self.assertEqual(event["category_key"], "outdoor")
        self.assertFalse(common.is_junk_event(event))

    def test_fetch_keeps_a_complete_fallback_when_detail_request_fails(self):
        listing_html = """
<div class="tile tile--one-quarter tile--single-height">
  <a href="/event/hofladen-alpakas-des-westens-geoeffnet" class="tile__link">
    <span class="tile__label-text">15.07.2026</span>
    <p class="header__head">Hofladen &quot;Alpakas des Westens&quot; geöffnet</p>
    <span class="icontext__text">Alpakas des Westens, Windeck</span>
  </a>
</div>
"""

        def fake_fetch(url, *args, **kwargs):
            if url == naturregion_sieg._URL:
                return listing_html
            raise TimeoutError("detail page timed out")

        with patch.object(common, "fetch_url", side_effect=fake_fetch), \
             patch.object(common, "log_source_error") as log_error:
            [event] = naturregion_sieg.fetch()

        self.assertIn("15.07.2026", event["description"])
        self.assertIn("Alpakas des Westens, Windeck", event["description"])
        self.assertTrue(event["description"].endswith("."))
        log_error.assert_called_once()
