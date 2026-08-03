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


if __name__ == "__main__":
    unittest.main()
