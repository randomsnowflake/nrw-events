import unittest
from unittest.mock import patch

from scripts.nrw_events.sources import siegburg
from scripts.nrw_events.sources import SOURCES


DETAIL_LINK = (
    "https://events.siegburg.de/Veranstaltungen/"
    "Gedenkstaetten-des-Holocaust-Vergangenheit-bewahren-Zukunft-gestalten.html"
)


DETAIL_HTML = """
<main>
  <div id="event_subtitle_wrapper">
    <span>Eine Ausstellung des Projektkurses Q1 der Gesamtschule am Michaelsberg</span>
  </div>
  <div id="event_description_wrapper">
    <div class="event_teaser_img_wrapper">
      <span class="image_copyright">© GSM Siegburg</span>
    </div>
    <div class="dwa_event_description_text">
      <span class="teaser">Ausstellung vom 1. Juli bis 18. Juli 2026</span>
      <p>
        <span class="image_wrapper">
          <img alt="Ausstellung der Gesamtschule am Michaelsberg Siegburg"
               title="Bildnachweis: GSM Siegburg">
        </span>
      </p>
    </div>
  </div>
  <div class="event-footer">Veranstaltungsort und Kontaktdaten</div>
</main>
"""


class SiegburgDetailEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.cache_env = patch.dict(
            "os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"})
        self.cache_env.start()
        siegburg.common._reset_detail_page_cache()

    def tearDown(self):
        siegburg.common._reset_detail_page_cache()
        self.cache_env.stop()

    def test_registry_uses_the_detail_enriching_fetcher(self):
        self.assertIs(SOURCES["Siegburg"], siegburg.fetch)

    def test_parser_combines_subtitle_and_body_without_image_metadata(self):
        description = siegburg._parse_detail_description(DETAIL_HTML)

        self.assertEqual(
            description,
            (
                "Eine Ausstellung des Projektkurses Q1 der Gesamtschule am Michaelsberg "
                "Ausstellung vom 1. Juli bis 18. Juli 2026"
            ),
        )
        self.assertNotIn("Bildnachweis", description)
        self.assertNotIn("Veranstaltungsort", description)

    def test_fetch_enriches_repeated_empty_events_with_one_detail_request(self):
        events = [
            {"title": "Gedenkstätten", "link": DETAIL_LINK, "description": ""},
            {"title": "Gedenkstätten", "link": DETAIL_LINK, "description": ""},
            {"title": "Yoga und Klang", "link": "https://example.test/yoga", "description": "Feed copy"},
        ]

        with patch.object(siegburg.common, "fetch_ical", return_value=events), \
                patch.object(siegburg.common, "fetch_url", return_value=DETAIL_HTML) as fetch_detail:
            enriched = siegburg.fetch()

        expected = (
            "Eine Ausstellung des Projektkurses Q1 der Gesamtschule am Michaelsberg "
            "Ausstellung vom 1. Juli bis 18. Juli 2026"
        )
        self.assertEqual(enriched[0]["description"], expected)
        self.assertEqual(enriched[1]["description"], expected)
        self.assertEqual(enriched[2]["description"], "Feed copy")
        fetch_detail.assert_called_once_with(DETAIL_LINK, timeout=15)

    def test_detail_failure_keeps_ical_events_available(self):
        events = [{"title": "Gedenkstätten", "link": DETAIL_LINK, "description": ""}]

        with patch.object(siegburg.common, "fetch_ical", return_value=events), \
                patch.object(siegburg.common, "fetch_url", side_effect=TimeoutError("detail timeout")), \
                patch.object(siegburg.common, "log_source_error") as log_error:
            result = siegburg.fetch()

        self.assertIn("findet", result[0]["description"])
        log_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
