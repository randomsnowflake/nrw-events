import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import much


DETAIL_LINK = (
    "https://www.much.de/willkommen/veranstaltungen/detail/"
    "19-07-2026_1400/gartencafe-der-solawi-much"
)


DETAIL_HTML = """
<main>
  <script type="application/ld+json">
    {
      "@type": "Event",
      "description": "Eine kleine Pause auf dem Weg ins Heck? Wir laden Sie herzlich auf einen Kaffee, Tee und hausgemachten Kuchen ein. Anmeldung: info@solawi-much.de"
    }
  </script>
  <div class="row event-teaser">
    <div class="teaser-text" itemprop="description">
      <p>Gartencafe der Solawi Much</p>
      <p>19.07.2026</p>
    </div>
  </div>
  <div class="row">
    <div class="teaser-text" itemprop="description">
      <p>Eine kleine Pause auf dem Weg ins Heck?</p>
      <p>Wir laden Sie herzlich auf einen Kaffee, Tee und hausgemachten Kuchen ein.</p>
      <p>Anmeldung: info<span style="display:none">random-noise</span>solawi-much.de</p>
    </div>
    <div class="news-text-wrap" itemprop="articleBody"></div>
  </div>
  <footer>Öffnungszeiten und Kontaktdaten der Gemeinde Much</footer>
</main>
"""


class MuchDetailEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.cache_env = patch.dict(
            "os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"})
        self.cache_env.start()
        much.common._reset_detail_page_cache()

    def tearDown(self):
        much.common._reset_detail_page_cache()
        self.cache_env.stop()

    def test_parser_selects_richest_official_description(self):
        description = much._parse_detail_description(
            DETAIL_HTML, "Gartencafe der Solawi Much")

        self.assertEqual(
            description,
            (
                "Eine kleine Pause auf dem Weg ins Heck? Wir laden Sie herzlich auf "
                "einen Kaffee, Tee und hausgemachten Kuchen ein. Anmeldung: "
                "info@solawi-much.de"
            ),
        )
        self.assertNotIn("random-noise", description)
        self.assertNotIn("19.07.2026", description)
        self.assertNotIn("Öffnungszeiten", description)

    def test_parser_ignores_title_only_placeholder(self):
        html = '<div class="teaser-text" itemprop="description">Trauer-Treff</div>'

        self.assertEqual(much._parse_detail_description(html, "Trauer Treff"), "")

    def test_structured_fallback_uses_date_time_and_venue_when_copy_is_empty(self):
        html = """
<script type="application/ld+json">
{
  "@type": "Event",
  "name": "Trauer Treff",
  "startDate": "2026-07-14T16:00:00",
  "endDate": "2026-07-14T17:30:00",
  "description": "",
  "location": {
    "@type": "Place",
    "name": "Amb. Hospizdienst Much",
    "address": {
      "streetAddress": "Dr. Wirtz Str. 6",
      "postalCode": "53804",
      "addressLocality": "Much"
    }
  }
}
</script>
"""

        context = much._structured_detail_context(html, "Trauer Treff")

        self.assertEqual(
            context["description"],
            common.factual_event_description(
                "Trauer Treff", date_value=datetime(2026, 7, 14, 16),
                time_text="16:00", end_time_text="17:30",
                venue="Amb. Hospizdienst Much, Dr. Wirtz Str. 6, 53804 Much",
                city="Much",
            ),
        )
        self.assertEqual(
            context["venue"],
            "Amb. Hospizdienst Much, Dr. Wirtz Str. 6, 53804 Much",
        )

    def test_fetch_enriches_repeated_empty_events_with_one_detail_request(self):
        events = [
            {"title": "Gartencafe der Solawi Much", "link": DETAIL_LINK, "description": ""},
            {"title": "Gartencafe der Solawi Much", "link": DETAIL_LINK, "description": ""},
            {"title": "Andere Veranstaltung", "link": "https://example.test", "description": "Feed copy"},
        ]

        with patch.object(much.common, "fetch_url", side_effect=["listing", DETAIL_HTML]) as fetch_url, \
                patch.object(much.common, "events_from_time_listing", return_value=events):
            enriched = much.fetch()

        expected = (
            "Eine kleine Pause auf dem Weg ins Heck? Wir laden Sie herzlich auf "
            "einen Kaffee, Tee und hausgemachten Kuchen ein. Anmeldung: "
            "info@solawi-much.de"
        )
        self.assertEqual(enriched[0]["description"], expected)
        self.assertEqual(enriched[1]["description"], expected)
        self.assertEqual(enriched[2]["description"], "Feed copy")
        self.assertEqual(fetch_url.call_count, 2)
        fetch_url.assert_any_call(much._URL, timeout=20)
        fetch_url.assert_any_call(DETAIL_LINK, timeout=20)

    def test_detail_failure_keeps_listing_events_available(self):
        events = [{"title": "Gartencafe", "link": DETAIL_LINK, "description": ""}]

        with patch.object(much.common, "fetch_url", side_effect=["listing", TimeoutError("detail timeout")]), \
                patch.object(much.common, "events_from_time_listing", return_value=events), \
                patch.object(much.common, "log_source_error") as log_error:
            result = much.fetch()

        self.assertIn("findet", result[0]["description"])
        log_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
