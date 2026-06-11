import unittest

from scripts.nrw_events.category_taxonomy import CATEGORIES, categorize_event


class CategoryTaxonomyTests(unittest.TestCase):
    def test_exports_more_than_the_original_coarse_categories(self):
        keys = [category["key"] for category in CATEGORIES]

        self.assertGreaterEqual(len(keys), 12)
        self.assertEqual(len(keys), len(set(keys)))
        for key in ["concert", "nightlife", "market", "food", "sports", "workshop", "cinema"]:
            self.assertIn(key, keys)

    def test_categorizes_clear_event_intent(self):
        cases = [
            ("", "Techno Party im Club", "", "nightlife"),
            ("", "Wochenmarkt Münsterplatz", "", "market"),
            ("", "Streetfood-Festival in Eitorf", "", "food"),
            ("", "Open-Air Kino Rheinaue", "", "cinema"),
            ("", "Rennradeln nach Feierabend", "", "sports"),
            ("", "Keramik-Workshop", "", "workshop"),
            ("", "A Cappella-Konzert", "", "concert"),
            ("", "Unklare Veranstaltung", "", "other"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)

    def test_known_bonn_fixture_regressions_use_specific_intent_before_broad_family_or_stage_terms(self):
        cases = [
            ("Märkte/Messen", "Kinderbücher-Flohmarkt", "", "market"),
            ("", "Linedance-Schnupperworkshops Donnerstags", "", "workshop"),
            ("", "Offener Theaterworkshop", "", "workshop"),
            ("", "Running City Tours - Joggen & Sightseeing verbinden", "", "sports"),
            ("", "Public Viewing Fußball Weltmeisterschaft 2026", "", "festival"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)

    def test_category_result_exposes_debug_reason_and_confidence(self):
        result = categorize_event("Märkte/Messen", "Kinderbücher-Flohmarkt", "")

        self.assertEqual(result["key"], "market")
        self.assertGreater(result["confidence"], 0)
        self.assertIn("market", result["reason"])

    def test_title_only_keywords_do_not_match_descriptions(self):
        category = categorize_event("", "Unklare Veranstaltung", "Treffpunkt am Markt")

        self.assertEqual(category["key"], "other")

    def test_generic_source_hint_with_many_categories_does_not_overpower_specific_page_context(self):
        category = categorize_event(
            "kommunal kultur markt ausstellung konzert führung",
            "New Perspectives in der Sammlung",
            "Frauke Dannert im Max Ernst Museum",
        )

        self.assertEqual(category["key"], "exhibition")

    def test_source_hint_does_not_turn_workshop_with_dance_word_into_stage(self):
        category = categorize_event("Tanz", "Linedance-Schnupperworkshops Donnerstags", "")

        self.assertEqual(category["key"], "workshop")


if __name__ == "__main__":
    unittest.main()
