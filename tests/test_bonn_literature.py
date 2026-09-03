import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events.quality import evaluate_event_quality
from nrw_events.sources import SOURCES, bonn_literature

from tests.helpers import patch_window

# Trimmed from https://www.parkbuchhandlung.de/veranstaltungen/. The listing
# repeats every card in an archive block further down the page; the duplicate
# below reproduces that.
PARK_HTML = """
<div class="mkdf-event-content mkdf-events43418">
  <div class="mkdf-event-date-location-holder">
    <div class="mkdf-event-date-holder"><div class="mkdf-event-date">27.09.2026</div></div>
    <div class="mkdf-event-location-holder"><div class="mkdf-event-location">Rhein&shy;hotel Dreesen</div></div>
  </div>
  <div class="mkdf-event-title-holder"><h5 class="mkdf-event-title">
    <a href="https://www.parkbuchhandlung.de/event/gianrico-carofiglio/" target="_blank">
      Gianrico Carofiglio &raquo;Der Horizont der Nacht&laquo;</a>
  </h5></div>
</div>
<div class="mkdf-event-content mkdf-events43082">
  <div class="mkdf-event-date-location-holder">
    <div class="mkdf-event-date-holder"><div class="mkdf-event-date">20.09.2026</div></div>
    <div class="mkdf-event-location-holder"><div class="mkdf-event-location">Schau&shy;spielhaus Bonn (Foyer)</div></div>
  </div>
  <div class="mkdf-event-title-holder"><h5 class="mkdf-event-title">
    <a href="https://www.parkbuchhandlung.de/event/stadtschreiberin/" target="_blank">
      Pr&auml;sentation der Bonner Stadtschreiberin 2026</a>
  </h5></div>
</div>
<div class="mkdf-event-content mkdf-events43418">
  <div class="mkdf-event-date-location-holder">
    <div class="mkdf-event-date-holder"><div class="mkdf-event-date">27.09.2026</div></div>
    <div class="mkdf-event-location-holder"><div class="mkdf-event-location">Rhein&shy;hotel Dreesen</div></div>
  </div>
  <div class="mkdf-event-title-holder"><h5 class="mkdf-event-title">
    <a href="https://www.parkbuchhandlung.de/event/gianrico-carofiglio/" target="_blank">
      Gianrico Carofiglio &raquo;Der Horizont der Nacht&laquo;</a>
  </h5></div>
</div>
"""

PARK_DETAIL_HTML = """
<div class="mkdf-event-header-time">17:00</div>
<div class="mkdf-event-header-price">18€</div>
<div class="mkdf-event-content-holder">
  <p>Der Autor liest aus seinem neuen Roman und spricht anschließend mit dem Publikum.</p>
</div>
<div class="mkdf-grid-row"></div>
"""


class BonnLiteratureSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 27), datetime(2026, 12, 31))

    def test_both_sources_are_registered(self):
        self.assertIs(SOURCES["Literaturhaus Bonn"], bonn_literature.fetch_literaturhaus)
        self.assertIs(SOURCES["Parkbuchhandlung"], bonn_literature.fetch_parkbuchhandlung)

    def test_glued_guillemet_seams_are_repaired(self):
        # The Literaturhaus CMS drops the line break around the work title.
        self.assertEqual(
            bonn_literature._normalize_title("Lukas Rietzschel»Sanditz«"),
            "Lukas Rietzschel »Sanditz«",
        )
        self.assertEqual(
            bonn_literature._normalize_title("MADAME NIELSEN »DAS ZEITGEISTERHAUS«MIT DANIEL"),
            "MADAME NIELSEN »DAS ZEITGEISTERHAUS« MIT DANIEL",
        )
        self.assertEqual(
            bonn_literature._normalize_title("Rhein­hotel Dreesen"), "Rheinhotel Dreesen"
        )

    def test_known_series_prefixes_are_separated_from_event_titles(self):
        cases = {
            "WORTREICHLUKAS RIETZSCHEL »SANDITZ«":
                "WORTREICH LUKAS RIETZSCHEL »SANDITZ«",
            "LESEZIRKELNORBERT SCHEUER »HOLUNDERHOLZ«":
                "LESEZIRKEL NORBERT SCHEUER »HOLUNDERHOLZ«",
            "LESUNG UND PERFORMANCEMADAME NIELSEN »DAS ZEITGEISTERHAUS«":
                "LESUNG UND PERFORMANCE MADAME NIELSEN »DAS ZEITGEISTERHAUS«",
            "CUT-UPCollagen und Texte gemeinsam gestalten":
                "CUT-UP Collagen und Texte gemeinsam gestalten",
            "TSCHECHIENMIT Dora Kaprálová":
                "TSCHECHIEN MIT Dora Kaprálová",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(bonn_literature._normalize_title(raw), expected)

    def test_literaturhaus_reading_circles_survive_the_reading_circle_rule(self):
        # The curated series names the discussed work, unlike a standing
        # library group, so it must not be dropped as a reading circle.
        for title in (
            "LESEZIRKEL DAVID SZALAY »WAS NICHT GESAGT WERDEN KANN«",
            "LESEZIRKELNORBERT SCHEUER »HOLUNDERHOLZ«",
        ):
            decision = evaluate_event_quality(
                {"title": title, "venue": "The Art of Books", "description": ""}
            )
            self.assertFalse(decision.should_drop, title)

    def test_parkbuchhandlung_parses_cards_and_collapses_repeats(self):
        events = bonn_literature.events_from_parkbuchhandlung_html(PARK_HTML)

        self.assertEqual(len(events), 2)
        by_date = {event["start_date"]: event for event in events}
        carofiglio = by_date["2026-09-27"]
        self.assertEqual(carofiglio["title"], "Gianrico Carofiglio »Der Horizont der Nacht«")
        self.assertEqual(carofiglio["venue"], "Rheinhotel Dreesen")
        self.assertEqual(carofiglio["city"], "Bonn-Bad Godesberg")
        self.assertEqual(carofiglio["category_key"], "talk")
        self.assertEqual(
            carofiglio["link"], "https://www.parkbuchhandlung.de/event/gianrico-carofiglio/"
        )
        self.assertTrue(carofiglio["description"])
        self.assertEqual(by_date["2026-09-20"]["venue"], "Schauspielhaus Bonn (Foyer)")

    def test_parkbuchhandlung_skips_cards_without_a_date(self):
        html = PARK_HTML.replace('<div class="mkdf-event-date">27.09.2026</div>', "")

        events = bonn_literature.events_from_parkbuchhandlung_html(html)

        self.assertEqual([event["start_date"] for event in events], ["2026-09-20"])

    def test_literaturhaus_wrapper_normalizes_titles_and_fills_descriptions(self):
        raw = [{
            "title": "Lukas Rietzschel»Sanditz«", "description": "",
            "start_date": "2026-09-17", "date": "2026-09-17", "time": "19:00",
            "venue": "Haus der Geschichte", "city": "Bonn",
            "source": "Literaturhaus Bonn",
        }]
        with patch.object(bonn_literature.common, "fetch_ical", return_value=raw):
            events = bonn_literature.fetch_literaturhaus()

        self.assertEqual(events[0]["title"], "Lukas Rietzschel »Sanditz«")
        self.assertIn("findet", events[0]["description"])

    def test_literaturhaus_classification_ignores_incidental_blurb_words(self):
        cases = {
            "Lukas Rietzschel »Sanditz«": "talk",
            "LIT.SPAZIERGANG": "outdoor",
            "FAHRT ZUR FRANKFURTER BUCHMESSE": "outdoor",
            "CUT-UP Collagen und Texte gemeinsam gestalten": "workshop",
            "LESUNG UND PERFORMANCE MADAME NIELSEN": "stage",
        }
        for title, expected in cases.items():
            event = {"title": title}
            bonn_literature._classify_literaturhaus(event)
            with self.subTest(title=title):
                self.assertEqual(event["category_key"], expected)

    def test_literaturhaus_uses_documented_departure_point_as_venue(self):
        raw = [{
            "title": "FAHRT ZUR FRANKFURTER BUCHMESSE",
            "description": (
                "Gemeinsame Busfahrt. Abfahrt um 8 Uhr ab Haus der Bildung, "
                "Ecke Bottlerplatz 1, Bonn."
            ),
            "start_date": "2026-10-10", "date": "2026-10-10",
            "time": "08:00–20:00", "venue": "", "city": "Bonn",
            "source": "Literaturhaus Bonn",
        }]
        with patch.object(bonn_literature.common, "fetch_ical", return_value=raw):
            events = bonn_literature.fetch_literaturhaus()

        self.assertEqual(events[0]["venue"], "Haus der Bildung")

    def test_parkbuchhandlung_literary_food_title_remains_a_talk(self):
        html = PARK_HTML.replace(
            "Gianrico Carofiglio &raquo;Der Horizont der Nacht&laquo;",
            "Leibspeisen. Eine kulinarische Biografie Deutschlands",
        )

        events = bonn_literature.events_from_parkbuchhandlung_html(html)

        event = next(
            item for item in events
            if item["title"] == "Leibspeisen. Eine kulinarische Biografie Deutschlands"
        )
        self.assertEqual(event["category_key"], "talk")

    def test_parkbuchhandlung_preserves_explicit_kabarett_as_stage_format(self):
        html = PARK_HTML.replace(
            "Gianrico Carofiglio &raquo;Der Horizont der Nacht&laquo;",
            "Kabarett mit Max Uthoff",
        )

        events = bonn_literature.events_from_parkbuchhandlung_html(html)

        event = next(item for item in events if item["title"] == "Kabarett mit Max Uthoff")
        self.assertEqual(event["category_key"], "stage")

    def test_parkbuchhandlung_enriches_time_price_and_description_once_per_link(self):
        calls = []

        def detail_fetcher(url):
            calls.append(url)
            return PARK_DETAIL_HTML

        events = bonn_literature.events_from_parkbuchhandlung_html(
            PARK_HTML, detail_fetcher
        )

        carofiglio = next(
            event for event in events if "Carofiglio" in event["title"]
        )
        self.assertEqual(carofiglio["time"], "17:00")
        self.assertEqual(carofiglio["price"], "18 €")
        self.assertFalse(carofiglio["all_day"])
        self.assertIn("spricht anschließend", carofiglio["description"])
        self.assertEqual(
            calls.count("https://www.parkbuchhandlung.de/event/gianrico-carofiglio/"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
