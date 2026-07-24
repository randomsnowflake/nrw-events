import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import SOURCES, lampert
from tests.helpers import patch_window


LAMPPERT_PAGE = """
<article class="post type-post">
  <header><h1 class="entry-title">53121 Bonn, Siemensstraße</h1></header>
  <div class="entry-content">
    <p>Siemensstraße<br>
    Nähe Autobahn-Verteilerkreis<br>
    Parkplatz<br>
    im Nahbereich ausgeschildert<br>
    jeden Samstag 08:00-14:00 Uhr (außer an Feiertagen)<br>
    alle typischen Waren erlaubt (Altwaren, Neuwaren von geringem Wert)<br>
    Verkauf 08:00-14:00 Uhr</p>
    <p><a href="https://www.google.com/maps/place/Siemensstra%C3%9Fe+26,+53121+Bonn">
      In Google Maps ansehen
    </a></p>
    <p><strong>Standmieten</strong><br>
    Trödel: pro lfdm. Trödel 10 € zzgl. 5 € Grundmiete pro Stand</p>
    <p><strong>Termine 2026</strong><br>
    jeden Samstag<br>
    (außer 03.10. und 26.12. – kein Markt wegen Feiertag)</p>
  </div>
</article>
"""


class LampertSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 9, 28), datetime(2026, 10, 12))

    def test_source_is_registered(self):
        self.assertIs(SOURCES["Lampert Märkte"], lampert.fetch)

    def test_parser_expands_only_in_window_saturdays_and_excludes_source_dates(self):
        events = lampert._events_from_page(LAMPPERT_PAGE)

        self.assertEqual([event["date"] for event in events], ["2026-10-10"])
        event = events[0]
        self.assertEqual(event["title"], "Flohmarkt Bonn Siemensstraße")
        self.assertEqual(event["time"], "08:00–14:00")
        self.assertEqual(event["city"], "Dransdorf")
        self.assertEqual(event["venue"], "Ehemalige Biskuithalle, Siemensstraße 26")
        self.assertEqual(event["source"], "Lampert Märkte")
        self.assertEqual(event["source_id"], "lampert-bonn-siemensstrasse")
        self.assertEqual(event["category_key"], "market")
        self.assertEqual(
            event["link"],
            "https://lampert-maerkte.de/53121-bonn-an-der-ehem-biskuithalle/",
        )
        self.assertNotIn("10 €", event["price"])
        self.assertIn("jeden Samstag", event["description"])

    def test_parser_returns_nothing_when_recurrence_or_year_is_missing(self):
        without_recurrence = LAMPPERT_PAGE.replace("jeden Samstag<br>", "Termine nach Ankündigung<br>", 1)
        without_year = LAMPPERT_PAGE.replace("Termine 2026", "Termine")

        self.assertEqual(lampert._events_from_page(without_recurrence), [])
        self.assertEqual(lampert._events_from_page(without_year), [])

    def test_fetch_uses_normal_warning_path_when_source_contract_breaks(self):
        with (
            patch.object(common, "fetch_url", return_value="<html>changed</html>"),
            patch.object(common, "log_source_error") as log_error,
        ):
            self.assertEqual(lampert.fetch(), [])

        log_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
