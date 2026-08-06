import unittest

from nrw_events.scoring import category_score


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


if __name__ == "__main__":
    unittest.main()
