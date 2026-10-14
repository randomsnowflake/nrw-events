import unittest
from datetime import datetime

from nrw_events.scoring import category_score
from nrw_events.sources.requested_venues import _events_from_pantheon

from tests.helpers import patch_window


class CategoryScoreTests(unittest.TestCase):
    def test_unmatched_event_has_neutral_weight(self):
        self.assertEqual(category_score("Sondertermin"), 1.0)

    def test_configured_demotions_are_not_hidden_by_a_floor(self):
        self.assertEqual(category_score("Workshop für Erwachsene"), 0.7)
        self.assertEqual(category_score("Sport am Abend"), 0.5)
        self.assertEqual(category_score("Reading class"), 0.4)
        self.assertEqual(category_score("Vorlesen für Kinder"), 0.2)

    def test_strongest_boost_and_demotion_are_multiplied(self):
        self.assertAlmostEqual(category_score("Sport und Konzert"), 0.75)

    def test_family_side_offer_does_not_demote_adult_destination_event(self):
        self.assertEqual(
            category_score("Weinfest mit Kinderquiz und Familienprogramm"),
            1.45,
        )

    def test_charitable_beneficiaries_are_not_the_audience(self):
        for text, expected in (
            ("Lach mal was mit wünschdirwas · Benefiz-Comedy-Kabarett für "
             "schwerkranke Kinder + Jugendliche Pantheon Theater Bonn kabarett kleinkunst", 1.3),
            ("Benefizkonzert für schwerkranke Kinder und Jugendliche", 1.5),
            ("Konzert zugunsten kranker Kinder", 1.5),
        ):
            with self.subTest(text=text):
                self.assertEqual(category_score(text), expected)

    def test_benefit_wording_does_not_hide_a_child_audience(self):
        for text in (
            "Kindertheater zugunsten kranker Kinder",
            "Benefiz-Comedy-Kabarett für schwerkranke Kinder + Jugendliche. "
            "Vorlesen für Kinder ab 6 Jahren",
            "Kabarett für Kinder",
            "Benefizkonzert für Kinder",
            "Theater für schwerkranke Kinder",
        ):
            with self.subTest(text=text):
                self.assertLessEqual(category_score(text), 0.25)

    def test_pantheon_benefit_event_clears_publication_score_floor(self):
        patch_window(self, datetime(2026, 10, 14), datetime(2026, 11, 10))
        # Official programme fragment #t647233, checked 2026-10-14.
        [event] = _events_from_pantheon("""
            <li id="t647233">
              <div class="event-date-1">14.</div>
              <div class="event-date-3">Oktober</div>
              <div class="event-time">20:00</div>
              <div class="event-location"><a>Pantheon</a></div>
              <h2 class="event-title">Lach mal was mit wünschdirwas ·
                Sandra Niggemann (Moderation) · Florian Schroeder · Matthias Reuter ·
                Nessi Tausendschön &amp; William Mackenzie · Sabine Domogala ·
                Benefiz-Comedy-Kabarett für schwerkranke Kinder + Jugendliche -
                Lach mal was mit wün</h2>
              <h3 class="event-type"></h3>
            </li>
        """)
        self.assertEqual(event["start_at"], "2026-10-14T20:00+02:00")
        self.assertEqual(event["link"], "https://www.pantheon.de/programm/#t647233")
        self.assertGreaterEqual(event["score"], 0.4)

    def test_matching_uses_casefold(self):
        self.assertEqual(category_score("KINDERPROGRAMM"), 0.2)

    def test_substring_misfires_do_not_score(self):
        # "sport" must not fire inside "Transport"
        self.assertEqual(category_score("Transport zum Flughafen"), 1.0)
        # "art" must not fire inside "Karten", "Quartier", "Start"
        self.assertEqual(category_score("Kartenvorverkauf im Quartier startet"), 1.0)
        # "live" must not fire inside "Oliver"
        self.assertEqual(category_score("Abend mit Oliver"), 1.0)
        # "tour" must not fire inside "Tourismusbüro"
        self.assertEqual(category_score("Tourismusbüro informiert"), 1.0)
        # "wein" must not fire inside "Schweinfurt"
        self.assertEqual(category_score("Busfahrt nach Schweinfurt"), 1.0)
        self.assertEqual(category_score("Kunststoff-Recycling"), 1.0)
        self.assertEqual(category_score("Weinen und Lachen: Kindertheater"), 0.2)
        self.assertEqual(category_score("Viertelfinale für Kinder"), 0.2)

    def test_exact_words_still_score(self):
        self.assertEqual(category_score("Sport im Verein"), 0.5)
        self.assertEqual(category_score("Konzert im Park"), 1.5)
        self.assertEqual(category_score("Weinfest am Rheinufer"), 1.45)

    def test_german_compounds_still_score(self):
        # word-prefix: keyword at the head of a compound
        self.assertEqual(category_score("Sportfest der Vereine"), 0.5)
        # "wein" (1.45) prefix-matches "Weinprobe" and outranks "weinprobe" (1.4)
        self.assertEqual(category_score("Weinprobe im Gewölbekeller"), 1.45)
        self.assertEqual(category_score("Konzertabend"), 1.5)
        # compound mode: keyword at the tail of a compound
        self.assertEqual(category_score("Jazzkonzert"), 1.5)
        self.assertEqual(category_score("Weihnachtsmarkt"), 1.1)
        self.assertEqual(category_score("Radtour entlang der Sieg"), 1.2)
        self.assertEqual(category_score("Kleinkunstabend"), 1.3)
        self.assertEqual(category_score("Fotoworkshop"), 0.7)
        self.assertEqual(category_score("Hardtechno"), 1.8)
        self.assertEqual(category_score("Radsport"), 0.5)

    def test_tour_inflections_score_without_matching_tourism(self):
        self.assertEqual(category_score("Touren durch Bonn"), 1.2)
        self.assertEqual(category_score("Stadttouren"), 1.2)
        self.assertEqual(category_score("Tourismusbüro informiert"), 1.0)
        self.assertEqual(category_score("Technologieforum"), 1.0)


if __name__ == "__main__":
    unittest.main()
