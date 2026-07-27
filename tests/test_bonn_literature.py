import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
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

    def test_a_glued_series_prefix_is_left_untouched(self):
        # "WORTREICHLUKAS" carries no detectable boundary; inventing one would
        # need a hardcoded list of series names.
        self.assertEqual(
            bonn_literature._normalize_title("WORTREICHLUKAS RIETZSCHEL »SANDITZ«"),
            "WORTREICHLUKAS RIETZSCHEL »SANDITZ«",
        )

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


if __name__ == "__main__":
    unittest.main()
