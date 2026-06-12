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

    def test_livetalk_is_forced_to_talk_not_concert(self):
        category = categorize_event(
            "kommunal kultur konzert",
            "Livetalk: Arthrose – was hilft wirklich?",
            "Live aus der Klinik mit Expertengespräch",
        )

        self.assertEqual(category["key"], "talk")
        self.assertEqual(category.get("confidence"), 1.0)
        self.assertEqual(category.get("reason"), "forced:talk")

    def test_current_classifier_regressions_avoid_broad_substring_traps(self):
        cases = [
            ("konzert", 'Handarbeitstreff "Em Ahle Kluster"', "", "other"),
            ("konzert", 'Frühstückszeit "Em Ahle Kluster"', "", "other"),
            ("", "Künstlerische Intervention: Mapping Waidmarkt – Soundwalk", "", "outdoor"),
            ("", "NEU! Die sanfte Art sich zu bewegen: Gymnastik mal tänzerisch!", "", "sports"),
            ("", "Rückbildungsgymnastik mit Babybetreuung", "", "sports"),
            ("", "English Club am Vormittag B1-B2", "", "other"),
            ("kommunal kultur konzert", "Livetalk: Arthrose der großen Gelenke", "Live aus der Klinik", "talk"),
            ("", "52. Jazz für Ohr und Gaumen: Andino Project", "", "concert"),
            ("", "TruckScout24 EHF FINAL4", "europäisches Spitzenhandball", "sports"),
            ("", "Hohes Venn 463", "Treffpunkt Himmeroder Wall", "outdoor"),
            ("", "18. Biker-Treffen der Biker in der Bundespolizei Sankt Augustin", "Live Musik am Abend", "festival"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)


if __name__ == "__main__":
    unittest.main()
