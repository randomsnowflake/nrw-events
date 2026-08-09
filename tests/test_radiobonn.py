import unittest

from nrw_events.sources import radiobonn
from nrw_events.validation import canonicalize_event
from nrw_events.sources import SOURCES


class RadioBonnLocationTests(unittest.TestCase):
    def test_adapter_is_registered(self):
        self.assertIs(SOURCES["Radio Bonn/Rhein-Sieg"], radiobonn.fetch)

    def test_specific_city_wins_over_bonn_mentions(self):
        text = "Eitorf Live auf dem Marktplatz, empfohlen von Radio Bonn"
        self.assertEqual(radiobonn._city_for(text), "Eitorf")

    def test_drachenfels_is_located_in_koenigswinter(self):
        text = "Wanderung am Drachenfels mit Blick auf Bonn und den Rhein"
        self.assertEqual(radiobonn._city_for(text), "Königswinter")

    def test_sieglar_marktplatz_is_located_in_troisdorf(self):
        text = "Sommer findet Stadt x Weinfest auf dem Sieglarer Marktplatz"

        self.assertEqual(radiobonn._city_for(text), "Troisdorf")
        self.assertEqual(radiobonn._venue_for(text, "Troisdorf"), "Sieglarer Marktplatz")

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

    def test_top_event_blocks_keep_precise_time_location_and_category(self):
        html = """
        <p><strong><u>SWB Sommerfestival: Sou Brasil - 09.08.2026</u></strong></p>
        <p>Beim SWB Sommerfestival steht Sou Brasil auf der Bühne am Parkrestaurant Rheinaue.
        Beginn ist um 14:30 Uhr. Der Eintritt ist frei.</p>
        <p><strong><u>Beachparty in Eudenbach - 08.08.2026</u></strong></p>
        <p>Ab 17 Uhr steigt die Beachparty auf dem Sportplatz in Eudenbach.</p>
        <p><strong><u>Landjugendparty und Traktorpulling in Lohmar - 08. - 09.08.2026</u></strong></p>
        <p>Die Landjugend Rhein-Sieg feiert am Samstag ab 17 Uhr in Wickuhl bei Lohmar.
        Am Sonntag folgt das Traktorpulling.</p>
        """

        events = radiobonn._events_from_html(html)
        by_title = {event["title"]: event for event in events}

        sommerfestival = by_title["SWB Sommerfestival: Sou Brasil"]
        self.assertEqual(sommerfestival["time"], "14:30")
        self.assertEqual(sommerfestival["city"], "Bonn")
        self.assertEqual(sommerfestival["venue"], "Parkrestaurant Rheinaue")
        self.assertEqual(sommerfestival["category_key"], "concert")
        self.assertEqual(sommerfestival["price"], "kostenlos")
        self.assertEqual(sommerfestival["admission_basis"], "explicit")
        self.assertEqual(canonicalize_event(sommerfestival).admission["basis"], "structured")

        beachparty = by_title["Beachparty in Eudenbach"]
        self.assertEqual(beachparty["city"], "Königswinter")
        self.assertEqual(beachparty["venue"], "Sportplatz Eudenbach")
        self.assertEqual(beachparty["category_key"], "nightlife")

        landjugend = by_title["Landjugendparty und Traktorpulling in Lohmar"]
        self.assertEqual(landjugend["venue"], "Wickuhl")
        self.assertEqual(landjugend["end_date"], "2026-08-09")

    def test_dattenfeld_is_assigned_to_windeck(self):
        self.assertEqual(radiobonn._city_for("Sankt Laurentius Kirmes Dattenfeld"), "Windeck")
        self.assertEqual(
            radiobonn._category_for("Sankt Laurentius Kirmes mit After Work Party"),
            "Fest",
        )

    def test_description_music_does_not_lock_an_unrelated_event_category(self):
        self.assertEqual(radiobonn._category_for("Rochuskirmes Kaldauen"), "Fest")
        self.assertEqual(radiobonn._category_for("Weinfest in Eitorf"), "Fest")
        self.assertEqual(radiobonn._category_for("Eine Zeitreise ins Jahr 1976"), "Event")
        self.assertEqual(radiobonn._category_for("Das Manifest"), "Event")

    def test_limited_free_admission_is_not_upgraded_to_free_for_everyone(self):
        html = """
        <p><strong><u>Familientag - 09.08.2026</u></strong></p>
        <p>Der Eintritt ist frei für Kinder, Erwachsene zahlen 8 Euro.</p>
        """
        [event] = radiobonn._events_from_html(html)

        self.assertEqual(event["price"], "")
        self.assertEqual(event["admission_basis"], "")
        self.assertIsNone(canonicalize_event(event).admission["isFree"])

        for qualifier in ("für Kinder", "mit Bonn-Ausweis"):
            qualified_html = f"""
            <p><strong><u>Familientag - 09.08.2026</u></strong></p>
            <p>Der Eintritt ist frei {qualifier}.</p>
            """
            [qualified_event] = radiobonn._events_from_html(qualified_html)
            self.assertEqual(qualified_event["price"], "kostenlos")
            self.assertEqual(qualified_event["admission_basis"], "inferred")
            self.assertEqual(
                canonicalize_event(qualified_event).admission["basis"],
                "inferred",
            )

    def test_repairs_obvious_rheinbach_domain_typo(self):
        self.assertEqual(
            radiobonn._best_event_link("Weitere Infos auf rehinbach.de"),
            "https://www.rheinbach.de/",
        )


if __name__ == "__main__":
    unittest.main()
