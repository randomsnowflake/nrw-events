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

    def test_general_imported_edge_case_signals_land_on_better_fit_pages(self):
        cases = [
            ("Musik/Konzert", "Indie Band EP Release Show", "", "concert"),
            ("Tanz", "Barhopping für Singles", "", "nightlife"),
            ("Bildung / Weiterbildung", "Freies Malen für Erwachsene", "", "workshop"),
            ("open air", "Puppenspiel auf der Kinderbühne", "", "kids"),
            ("", "Jazz für Ohr und Gaumen", "", "concert"),
            ("", "Autorenlesung Udo Weinbörner", "", "talk"),
            ("", "Spiele ausprobieren", "Brettspiel-Event zum Ausprobieren der nominierten Spiele", "other"),
            ("", "Wanderung mit Weinmomenten", "", "food"),
            ("", "Andino Project", "", "other"),
            ("", "Live-Band im Biergarten", "", "concert"),
            ("", "Garden Party im Stadtgarten", "", "outdoor"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)

    def test_current_feed_qa_keyword_regressions(self):
        cases = [
            ("", "Bad Bodendorfer Freitagsmarkt", "", "market"),
            ("", "Büchermarkt zur Reisezeit", "", "market"),
            ("", "Boule auf der Insel Grafenwerth", "", "sports"),
            ("", "Schlemmerabend", "", "food"),
            ("", "Singen & Grillen am Bach", "", "food"),
            ("", "Jubiläum Sing & Swing", "", "concert"),
            ("", "Brasilianische Hits mit dem Duo Bailae", "Brasilien Forro Samba", "concert"),
            ("", "Look at my toys!", "HackerSpace Meetup", "talk"),
            ("", "Cirque Buffon - Carrousel", "", "stage"),
            ("", "Literatur-Klatsch: Born this way", "", "talk"),
            ("", "Chris Warnat liest aus ihren Krimis", "", "talk"),
            ("", "Blick hinter die Kulissen der Steyler Mission", "", "outdoor"),
            ("", "Animany Convention Troisdorf 2026", "", "festival"),
            ("", "Sportwochenende des SV Leimersdorf", "", "sports"),
            ("", "Gag-Schreiben", "", "workshop"),
            ("", "SchachXperten", "", "sports"),
            ("Sonstige Veranstaltung", "Foto Club Wachtberg - Clubabend", "Im Fotoclub Wachtberg treffen sich Fotoamateure.", "other"),
            ("", 'Schumanns Carneval und von Ravel die "mirroirs"', "Klavierabend", "concert"),
            ("", "Pop-up-WeinLounge im Park", "Sommerlicher Weinausschank", "food"),
            ("concert", "Montez @ KUNST!RASEN", "", "concert"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)

    def test_feed_quality_gate_regressions_avoid_substrings_and_use_domain_intent(self):
        cases = [
            (
                "",
                "Persönliche Hilfestellung für eMedien",
                "Spezifische Problemlösung für Onleihe und Libby (Overdrive). Im Rahmen der Reihe Digitale Werkstatt.",
                "workshop",
            ),
            (
                "stadtteilfest market kirmes outdoor local",
                "Deutsch Holländischer Stoffmarkt",
                "Deutsch Holländischer Stoffmarkt, Münsterplatz",
                "market",
            ),
            ("", "Sommerleseclub 2026", "Anmeldung in der Stadtbücherei", "kids"),
            ("", "Lesesommer RLP", "", "kids"),
            ("Vorträge/Lesungen/Diskussionen", "Das Philosophische Café - Thema: Populismus", "", "talk"),
            ("", "Präventionsabend: Risiken im Netz – Fake News, Cybercrime & Co.", "Für alle mit und ohne schulischen Bezug", "talk"),
            ("", "Kaffee, Kuchen und KI", "Künstliche Intelligenz entdecken im Interim der Zentralbibliothek.", "talk"),
            ("", "NO GO – Performance im öffentlichen Raum", "Performance von Angie Hiesl und Roland Kaiser", "stage"),
            ("", "Fortis Colonia: Fort VI, Deckstein", "Kölner Festungstage", "outdoor"),
            ("", "AI26 – The Lamarr Conference on Artificial Intelligence", "Internationale KI-Konferenz im WCCB mit Speakern aus Wissenschaft und Wirtschaft.", "talk"),
            ("", "Um drei Ecken gedacht - Rechenschieber und Vermessung", "Sonderausstellung im Arithmeum", "exhibition"),
            ("", "Adenauer auf der Wolke", "Himmlische Karikaturen zum 150. Geburtstag", "exhibition"),
            ("concert", "Alien Fight Club @ Alte VHS", "Songkick concert listing", "concert"),
            ("Vorträge/Lesungen/Diskussionen", "Openair-Kino \u201eSpillover\u201c & Diskussion", "Filmvorführung mit Gespräch", "cinema"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)

    def test_fest_suffix_still_catches_real_festivals_without_matching_hilfestellung(self):
        cases = [
            ("", "Sommerfest Oberbachem", "", "festival"),
            ("", "Feuerwehrfest in Winterscheid", "", "festival"),
            ("", "Fest der Verbundenheit", "", "festival"),
            ("", "Persönliche Hilfestellung", "", "workshop"),
            ("", "Kölner Festungstage", "", "outdoor"),
            ("kommunal kultur ausstellung konzert führung", "Frischemarkt in der Innenstadt", "Regionale Frischeprodukte", "market"),
            ("", "Fantomaus – Plötzlich Superheld", "Ein musikalisches Lese-Abenteuer mit Autor und Musiker.", "kids"),
            ("", "Quiltingtreff", "Nähkunst mit der Hand – gemeinsam Quilten in der Stadtteilbibliothek.", "workshop"),
            ("konzert", "Rat (öffentliche Sitzung)", "", "other"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)


if __name__ == "__main__":
    unittest.main()
