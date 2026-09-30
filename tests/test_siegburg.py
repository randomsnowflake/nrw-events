import unittest
from unittest.mock import patch

from nrw_events.sources import SOURCES, siegburg

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
                "Eine Ausstellung des Projektkurses Q1 der Gesamtschule am Michaelsberg"
                "\n\n"
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

        # Subtitle and body are separate blocks on the page and stay separate
        # paragraphs here.
        expected = (
            "Eine Ausstellung des Projektkurses Q1 der Gesamtschule am Michaelsberg"
            "\n\n"
            "Ausstellung vom 1. Juli bis 18. Juli 2026"
        )
        self.assertEqual(enriched[0]["description"], expected)
        self.assertEqual(enriched[1]["description"], expected)
        self.assertEqual(enriched[2]["description"], "Feed copy")
        fetch_detail.assert_called_once_with(DETAIL_LINK, timeout=15)

    def test_source_detail_budget_falls_back_without_dropping_events(self):
        second_link = "https://events.siegburg.de/Veranstaltungen/Zweiter-Termin.html"
        events = [
            {"title": "Erster Termin", "link": DETAIL_LINK, "description": ""},
            {"title": "Zweiter Termin", "link": second_link, "description": ""},
        ]

        with patch.dict("os.environ", {"NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS": "45"}), \
                patch.object(siegburg.rc.time, "monotonic", side_effect=[100, 100, 146]), \
                patch.object(siegburg.common, "event_in_window", return_value=True), \
                patch.object(siegburg.common, "fetch_ical", return_value=events), \
                patch.object(siegburg.common, "fetch_url", return_value=DETAIL_HTML) as fetch_detail:
            enriched = siegburg.fetch()

        fetch_detail.assert_called_once_with(DETAIL_LINK, timeout=15)
        self.assertEqual(2, len(enriched))
        self.assertIn("findet", enriched[1]["description"])

    def test_detail_failure_keeps_ical_events_available(self):
        events = [{"title": "Gedenkstätten", "link": DETAIL_LINK, "description": ""}]

        with patch.object(siegburg.common, "fetch_ical", return_value=events), \
                patch.object(siegburg.common, "fetch_url", side_effect=TimeoutError("detail timeout")), \
                patch.object(siegburg.common, "log_source_error") as log_error:
            result = siegburg.fetch()

        self.assertIn("findet", result[0]["description"])
        log_error.assert_called_once()

    def test_truncated_feed_copy_is_replaced_by_complete_detail_text(self):
        events = [{
            "title": "Siegburger Stadtfest",
            "link": "https://events.siegburg.de/Veranstaltungen/Siegburger-Stadtfest-2.html",
            "description": "Dich erwarten drei Bühnen […]",
        }]

        with patch.object(siegburg.common, "event_in_window", return_value=True), \
                patch.object(siegburg.common, "fetch_ical", return_value=events), \
                patch.object(siegburg.common, "fetch_detail_url", return_value=DETAIL_HTML):
            [enriched] = siegburg.fetch()

        self.assertNotIn("[…]", enriched["description"])
        self.assertIn("Ausstellung vom 1. Juli", enriched["description"])


if __name__ == "__main__":
    unittest.main()


class ICalPriceTests(unittest.TestCase):
    """The feed's X-PRICE keeps commas and umlauts the detail JSON-LD drops."""

    def test_x_price_is_the_published_admission(self):
        from dataclasses import replace
        from datetime import datetime

        from nrw_events import common, core, validation
        from nrw_events.runtime import EventWindow

        from .helpers import make_runner_env

        raw = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:doc-esser\r\n"
            "SUMMARY:Doc Esser & Band\r\nDTSTART;TZID=Europe/Berlin:20260918T200000\r\n"
            "LOCATION:Kubana\\, Siegburg\r\nX-PRICE:22\\,00 €\\, Reduziert: 19\\,00 €\r\n"
            "END:VEVENT\r\nBEGIN:VEVENT\r\nUID:frei\r\nSUMMARY:Stadtführung\r\n"
            "DTSTART;TZID=Europe/Berlin:20260919T110000\r\nX-PRICE:Frei\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        with make_runner_env() as environment:
            context = replace(environment.context(), window=EventWindow(datetime(2026, 9, 3), datetime(2026, 12, 1)))
            old_window = (common.DAYS_AHEAD, common.TODAY, common.END_DATE)
            token = common.configure_context(context)
            try:
                paid, free = (
                    validation.canonicalize_event(event).to_dict()
                    for event in core.parse_ical(raw, "https://siegburg.example/feed.ics", "Siegburg", "Siegburg")
                )
            finally:
                common.reset_runtime(token)
                common.DAYS_AHEAD, common.TODAY, common.END_DATE = old_window
                common._configure_date_reference(old_window[1])

        self.assertEqual(paid["price"], "22,00 €, Reduziert: 19,00 €")
        self.assertIs(paid["admission"]["isFree"], False)
        self.assertEqual(paid["admission"]["amount"], 19)
        self.assertEqual(free["price"], "kostenlos")
        self.assertIs(free["admission"]["isFree"], True)
