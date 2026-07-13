import unittest

from scripts.nrw_events.sources import radiobonn
from scripts.nrw_events.sources import SOURCES


class RadioBonnLocationTests(unittest.TestCase):
    def test_adapter_is_registered(self):
        self.assertIs(SOURCES["Radio Bonn/Rhein-Sieg"], radiobonn.fetch)

    def test_specific_city_wins_over_bonn_mentions(self):
        text = "Eitorf Live auf dem Marktplatz, empfohlen von Radio Bonn"
        self.assertEqual(radiobonn._city_for(text), "Eitorf")

    def test_meeting_point_wins_over_organizer_location(self):
        text = (
            "Führung der VHS Bornheim/Alfter. Treffpunkt ist am Legionslager "
            "in der Graurheindorfer Straße in Bonn."
        )
        self.assertEqual(radiobonn._city_for(text), "Bonn")

    def test_configured_meeting_point_city_wins_over_hinted_organizer_location(self):
        text = "Veranstaltet von der VHS Alfter. Treffpunkt ist am Rathaus in Bornheim."
        self.assertEqual(radiobonn._city_for(text), "bornheim")

    def test_prefers_direct_event_anchor_over_radio_article(self):
        description = (
            'Alle Infos gibt es <a href="https://www.hennef.de/veranstaltungen/'
            'wanderung/?occurrence=2026-07-18&amp;source=radio">hier</a>.'
        )

        self.assertEqual(
            radiobonn._best_event_link(description),
            "https://www.hennef.de/veranstaltungen/wanderung/?occurrence=2026-07-18&source=radio",
        )

    def test_uses_plain_organizer_domain_when_no_anchor_exists(self):
        self.assertEqual(
            radiobonn._best_event_link("Tickets und Infos gibt es auf urban-colour.com."),
            "https://urban-colour.com",
        )

    def test_ignores_radio_self_links_and_non_web_links(self):
        description = (
            '<a href="mailto:veranstaltungen@radiobonn.de">Mail</a> '
            '<a href="https://www.radiobonn.de/artikel/weitere-tipps">Weitere Tipps</a>'
        )

        self.assertEqual(radiobonn._best_event_link(description), radiobonn.URL)

    def test_parses_same_month_multi_day_range(self):
        title, start, end = radiobonn._split_title_dates(
            "Birker Kirmes - 10. - 12.07.2026"
        )

        self.assertEqual(title, "Birker Kirmes")
        self.assertEqual(start.strftime("%Y-%m-%d"), "2026-07-10")
        self.assertEqual(end.strftime("%Y-%m-%d"), "2026-07-12")

    def test_parses_compact_ampersand_range(self):
        title, start, end = radiobonn._split_title_dates(
            "Sommerfest - 04. & 05.07.2026"
        )

        self.assertEqual(title, "Sommerfest")
        self.assertEqual(start.strftime("%Y-%m-%d"), "2026-07-04")
        self.assertEqual(end.strftime("%Y-%m-%d"), "2026-07-05")

    def test_parses_cross_month_range(self):
        title, start, end = radiobonn._split_title_dates(
            "Wintermarkt - 31.12. - 02.01.2027"
        )

        self.assertEqual(title, "Wintermarkt")
        self.assertEqual(start.strftime("%Y-%m-%d"), "2026-12-31")
        self.assertEqual(end.strftime("%Y-%m-%d"), "2027-01-02")


if __name__ == "__main__":
    unittest.main()
