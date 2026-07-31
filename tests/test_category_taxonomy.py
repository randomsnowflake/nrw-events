import json
import tempfile
import unittest
from pathlib import Path

from nrw_events.category_taxonomy import (
    CATEGORIES,
    category_cache_key,
    categorize_event,
    configure_fallback_cache,
)


class CategoryTaxonomyTests(unittest.TestCase):
    def tearDown(self):
        configure_fallback_cache()

    def test_compound_event_formats_classify_without_source_bags(self):
        cases = (
            ("b’future-Journalismusfestival", "festival"),
            ("Herbstkirmes Duisdorf", "festival"),
            ("Repaircafe im Selbstwerk Bonn", "workshop"),
            ("AfterJobParty Museumsmeile", "nightlife"),
            ("Hennefer Radsporttag", "sports"),
            ('ADFC-Feierabendtour "Über Berg und Tal"', "outdoor"),
        )

        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event("", title, "")["key"], expected)

    def test_compound_rules_do_not_turn_topic_words_into_event_formats(self):
        cases = (
            "Vortrag zur Sportwissenschaft",
            "Debatte über den Arbeitsmarkt 2030",
            "Forschung zur Festivalkultur",
        )

        for title in cases:
            with self.subTest(title=title):
                self.assertNotIn(
                    categorize_event("", title, "")["key"],
                    {"sports", "market", "festival"},
                )

    def test_locked_source_default_overrides_conflicting_title_format(self):
        result = categorize_event(
            "festival open air",
            "Rahmenprogramm: Führung und Filmvorführung",
            "Begleitprogramm der Internationalen Stummfilmtage.",
            default_category_key="cinema",
            category_locked=True,
        )

        self.assertEqual(result["key"], "cinema")
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["reason"], "source:locked-default:cinema")

    def test_unlocked_default_is_only_a_fallback(self):
        concert = categorize_event(
            "",
            "Jazzkonzert im Foyer",
            default_category_key="stage",
        )
        unknown = categorize_event(
            "",
            "Sondertermin",
            default_category_key="stage",
        )

        self.assertEqual(concert["key"], "concert")
        self.assertEqual(unknown["key"], "stage")
        self.assertEqual(unknown["confidence"], 1.0)
        self.assertEqual(unknown["reason"], "source:default:stage")

    def test_focused_source_bag_clears_confidence_threshold(self):
        result = categorize_event("Musik", "Sondertermin", "")

        self.assertEqual(result["key"], "concert")
        self.assertEqual(result["confidence"], 0.6)

    def test_einfuehrung_does_not_trigger_guided_tour_rule(self):
        result = categorize_event(
            "",
            'Martin Booms – Philosophie im Kino – "Einführung, Film & Diskussion"',
            "",
        )

        self.assertEqual(result["key"], "cinema")
        self.assertNotEqual(result["reason"], "forced:outdoor-title-format")

    def test_reviewed_cache_resolves_unknown_series_without_an_external_classifier(self):
        cache_key = category_cache_key("example-source", "Jeden Dienstag: Sondertermin")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "categories.json"
            path.write_text(json.dumps({
                "version": 1,
                "entries": {
                    cache_key: {"key": "talk", "confidence": 0.9, "reason": "editorial-review"},
                },
            }), encoding="utf-8")
            configure_fallback_cache(str(path))

            result = categorize_event(
                "",
                "Jeden Dienstag: Sondertermin",
                source_id="example-source",
            )

        self.assertEqual(result["key"], "talk")
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["reason"], "fallback:cache:editorial-review")

    def test_category_lock_requires_a_valid_explicit_default(self):
        with self.assertRaises(ValueError):
            categorize_event("", "Termin", category_locked=True)
        with self.assertRaises(ValueError):
            categorize_event("", "Termin", default_category_key="not-real")

    def test_general_intent_terms_reduce_unclassified_events(self):
        cases = {
            "Feriencamp des VfB": "kids",
            "Brotbacken im Steinofen": "workshop",
            "FSFE Community Meeting": "talk",
            "Sommerferienaktion mit Juppi": "kids",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(categorize_event("", title)["key"], expected)

    def test_exports_more_than_the_original_coarse_categories(self):
        keys = [category["key"] for category in CATEGORIES]

        self.assertGreaterEqual(len(keys), 12)
        self.assertEqual(len(keys), len(set(keys)))
        for key in ["concert", "nightlife", "market", "food", "sports", "workshop", "cinema", "activities"]:
            self.assertIn(key, keys)

    def test_activities_and_meetings_use_explicit_public_event_signals(self):
        cases = [
            ("", "Offener Spieletreff", "", "activities"),
            ("", "After Work Spieleabend", "", "activities"),
            ("", "Spielenachmittag", "", "activities"),
            ("", "Gemütlicher Kaffeeklatsch – gemeinsam statt einsam", "", "activities"),
            ("", "Alzheimer-Selbsthilfegruppe", "", "activities"),
            ("", "Foto Club Wachtberg – Clubabend", "", "activities"),
            ("", "Buchtreff", "", "talk"),
            ("", "Radlertreff des ADFC", "", "sports"),
            ("", "18. Biker-Treffen", "Live-Musik am Abend", "festival"),
            ("", "Treffpunkt im Park", "", "outdoor"),
            ("", "Mitgliederversammlung des Vereins", "", "other"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)

    def test_contextual_formats_require_corroborating_reusable_evidence(self):
        cases = [
            (
                "",
                "Abendliche Ruhezeit",
                "Die Teilnehmenden meditieren gemeinsam; das Angebot ist offen für alle.",
                "activities",
            ),
            (
                "",
                "Kreativwoche",
                "Gemeinsam schreiben, erzählen und gestalten wir; dabei entstehen eigene Hefte.",
                "workshop",
            ),
            (
                "",
                "Mobil bleiben",
                "Wir frischen Verkehrsregeln auf, bauen Unsicherheiten ab und stärken Fahrfähigkeiten.",
                "workshop",
            ),
            (
                "",
                "Dunkler Donnerstag",
                "Ein Abend mit Gothic, Dark Wave und düsteren Klängen im Live Club.",
                "concert",
            ),
            (
                "",
                "Einladung in die Eifel",
                "Du magst Tiere? Dann komm mit uns in die Eifel.",
                "outdoor",
            ),
            (
                "",
                "20 Jahre Nachbarschaftshilfe e. V.",
                "Wir feiern von Mittag bis Abend.",
                "festival",
            ),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    categorize_event(source_category, title, description)["key"],
                    expected,
                )

        # One vague word must not be enough to invent a format.
        self.assertEqual(categorize_event("", "Gemeinsamer Abend", "")["key"], "other")
        self.assertEqual(categorize_event("", "Hommage", "")["key"], "other")

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

    def test_seasonal_market_names_are_markets_without_generic_source_hints(self):
        for title in (
            "Bonner Weihnachtsmarkt",
            "Duisdorfer Adventsmarkt",
            "Nikolausmarkt Beuel",
            "Bonner Dreikönigsmarkt",
            "Kessenicher Herbstmarkt",
            "Frühlingsmarkt in der Altstadt",
            "Töpfermarkt Bonn",
            "Kunsthandwerkermarkt",
            "Schallplattenbörse",
            "Feierabendmarkt - meet&eat in Wesseling",
            "Vieh- und Krammarkt",
        ):
            with self.subTest(title=title):
                self.assertEqual(categorize_event("", title)["key"], "market")

    def test_known_bonn_fixture_regressions_use_specific_intent_before_broad_family_or_stage_terms(self):
        cases = [
            ("Märkte/Messen", "Kinderbücher-Flohmarkt", "", "market"),
            ("", "Kindersachenbasar Rund ums Kind", "", "market"),
            ("", "Antik&Design in der Kölner Flora", "", "market"),
            (
                "flohmarkt second hand markt",
                "Kindersachen Flohmarkt, Förderverein 1. BC Beuel",
                "Bekleidung für Kinder und Jugendliche",
                "market",
            ),
            (
                "flohmarkt second hand markt",
                "Bonn, Fashion, Family & Kids Markt im Telekom Dome",
                "Fashion, Family & Kids Markt",
                "market",
            ),
            ("", "Linedance-Schnupperworkshops Donnerstags", "", "workshop"),
            ("", "Offener Theaterworkshop", "", "workshop"),
            ("", "Running City Tours - Joggen & Sightseeing verbinden", "", "sports"),
            ("", "Public Viewing Fußball Weltmeisterschaft 2026", "", "festival"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)

    def test_neighbourhood_calendar_titles_do_not_inherit_a_blanket_family_label(self):
        cases = {
            "BV Roleber-Gielgen / BV Holzlar: Herbstfahrt": "outdoor",
            "Mühlenverein: Tag des offenen Denkmals": "festival",
            "Martinszug Holzlar": "kids",
            "Proklamation und Karneval Om Berg": "festival",
            "BV Holzlar: Weihnachtsbaum schmücken": "workshop",
            "BV Kohlkaul: Martinimarkt": "market",
            "BV Holzlar: Geburtstagskaffee": "other",
            "BV Kohlkaul: Weihnachtsfeier": "festival",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(
                    categorize_event("stadtteil verein gemeinschaft", title)["key"],
                    expected,
                )

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

    def test_plain_keywords_do_not_match_inside_unrelated_words(self):
        cases = [
            ("Sonstige Veranstaltung", "Ablauf der Mitgliederversammlung", "", "other"),
            ("Vorträge/Lesungen/Diskussionen", "Diskurs über Demokratie", "", "talk"),
            ("Sonstige Veranstaltung", "Kunststoffe im Alltag", "", "other"),
            ("Musik/Konzert", "Tournee-Auftakt", "", "concert"),
            ("Sonstige Veranstaltung", "Familientag im Kloster", "", "other"),
            ("Umwelt", "Schadstoff und Elektro-Kleinteile-Mobil", "", "other"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    categorize_event(source_category, title, description)["key"],
                    expected,
                )

    def test_known_indoor_museum_tours_are_exhibitions_without_reclassifying_outdoor_tours(self):
        indoor_cases = [
            (
                "Bundeskunsthalle Kultur Veranstaltung",
                "Führung: Peter Hujar",
                "",
                "Bundeskunsthalle",
                "Bundeskunsthalle",
            ),
            (
                "Kultur",
                "Studentische Sonntagsführung im Akademischen Kunstmuseum Bonn",
                "",
                "Akademisches Kunstmuseum Bonn",
                "Universität Bonn",
            ),
            (
                "Kultur",
                "Kuratorenführung durch die Ausstellung Paolo Porelli",
                "",
                "Stadtmuseum im Kulturhaus",
                "Siegburg",
            ),
        ]
        for source_category, title, description, venue, source in indoor_cases:
            with self.subTest(title=title):
                result = categorize_event(
                    source_category,
                    title,
                    description,
                    venue=venue,
                    source=source,
                )
                self.assertEqual(result["key"], "exhibition")
                self.assertEqual(result["reason"], "forced:indoor-museum-guided-tour")

        outdoor_cases = [
            ("Öffentliche Führung Kriminalistischer Stadtrundgang", "Eingang Stadtmuseum Siegburg"),
            ("Öffentliche Führung Siegburg für Entdecker", "Eingang des Stadtmuseums"),
            ("Sonntagsführung im Botanischen Garten", "Botanische Gärten Bonn"),
            ("Führung Rund um Burg Wissem", "Burg Wissem"),
        ]
        for title, venue in outdoor_cases:
            with self.subTest(title=title):
                result = categorize_event(
                    "Kultur",
                    title,
                    "",
                    venue=venue,
                    source="Siegburg",
                )
                self.assertEqual(result["key"], "outdoor")

    def test_compound_film_formats_are_strong_but_incidental_standalone_film_is_weak(self):
        cases = [
            ("Kultur", "Dokumentarfilm über Bonn", "", "cinema"),
            ("Kultur", "Filmvorstellung: Riefenstahl (2024)", "", "cinema"),
            ("Fest/Festival", "Kurzfilmfestival", "", "cinema"),
            ("Kino & Film", "Filmnächte in Andernach", "", "cinema"),
            (
                "Theater/Bühne",
                "Die Göttliche Komödie nach Dante",
                "Die Inszenierung wurde später auch als Film veröffentlicht.",
                "stage",
            ),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    categorize_event(source_category, title, description)["key"],
                    expected,
                )

    def test_weak_hint_does_not_expand_a_focused_source_category_bag(self):
        category = categorize_event(
            "Elektro Konzert Ausstellung",
            "Unbekannte Abendveranstaltung",
            "",
        )

        self.assertEqual(category["key"], "concert")

    def test_ambiguous_signals_still_work_when_corroborated(self):
        cases = [
            ("Nachtleben", "Elektro Party", "", "nightlife"),
            (
                "Musik/Konzert",
                "GA-Sommergarten – Albie Donnelly's Supercharge",
                "Heute gibt es wieder ein GA-Sommergarten-Konzert mit Live-Musik.",
                "concert",
            ),
            (
                "Allgemein Sinzig",
                "Energie tanken im Kräutergarten",
                "Entspannungsübungen inmitten der Natur zwischen Kräutern und Wiesen.",
                "outdoor",
            ),
            (
                "Natur und Umwelt",
                "Tag der offenen Gemüsegartenpforte",
                "Besucher können den ökologischen Garten besichtigen.",
                "outdoor",
            ),
            ("Kino & Film", "Filmabend", "Öffentliche Vorführung im Kino", "cinema"),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    categorize_event(source_category, title, description)["key"],
                    expected,
                )

    def test_current_classifier_regressions_avoid_broad_substring_traps(self):
        cases = [
            ("konzert", 'Handarbeitstreff "Em Ahle Kluster"', "", "activities"),
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
            (
                "Allgemein Rheinbach",
                "Gedenken 5. Jahrestag der Unwetterkatastrophe",
                "Im Anschluss gehen wir gemeinsam zur Erinnerungsstele in der Neugartenstraße.",
                "other",
            ),
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
            ("", "Spiele ausprobieren", "Brettspiel-Event zum Ausprobieren der nominierten Spiele", "activities"),
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
            ("", "Lieblingsbücher – Lesezirkel in der Bücherbrücke", "", "talk"),
            ("", "Blick hinter die Kulissen der Steyler Mission", "", "outdoor"),
            ("", "Animany Convention Troisdorf 2026", "", "festival"),
            ("", "Sportwochenende des SV Leimersdorf", "", "sports"),
            ("", "Gag-Schreiben", "", "workshop"),
            ("", "SchachXperten", "", "sports"),
            ("Sonstige Veranstaltung", "Foto Club Wachtberg - Clubabend", "Im Fotoclub Wachtberg treffen sich Fotoamateure.", "activities"),
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
            ("concert", "Alien Fight Club @ Alte VHS", "Concert listing", "concert"),
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

    def test_general_matching_prefers_event_format_over_substrings_and_broad_source_bags(self):
        cases = [
            (
                "Klimaschutz kommunal lokal markt kultur",
                "Info- und Aktionswochen zu Solar und Wärmepumpe",
                "Die zentralen Technologien der Energiewende werden erklärt.",
                "talk",
            ),
            (
                "Aktion/Workshop, Kostenlos, Tanz",
                "Mehrtägiges Hip-Hop-Kunstprojekt im HDJ",
                "Tanz, Rap, DJing und Graffiti für Jugendliche.",
                "workshop",
            ),
            (
                "kommunal kultur markt ausstellung konzert führung",
                "Kreativer Hüttenbau",
                "Ein Pädagoge baut mit den Teilnehmenden verschiedene Behausungen.",
                "workshop",
            ),
            (
                "sankt augustin lokal kultur markt fest sport natur",
                "Sport im Park",
                "Kostenlos bewegen und draußen mitmachen.",
                "sports",
            ),
            (
                "naturregion outdoor kultur markt",
                "Ferienspaß: Tischtennis für alle",
                "Spiel und Teamgeist in der Turnhalle.",
                "sports",
            ),
            (
                "Ausstellungen, Barrierefrei",
                "Werden zwischen Materie, Geschichte und Natur",
                "In der Ausstellung zeigen zwei Keramikkünstler ihre Arbeiten.",
                "exhibition",
            ),
            (
                "Ausstellung, Fest/Festival",
                "Sundowner Bar",
                "Elektronische Musik, Kunst, kühle Drinks und Fingerfood.",
                "nightlife",
            ),
            (
                "lokal kultur markt",
                "Kreativsommer im Museum",
                "Kleine Bastelprofis gestalten gemeinsam kreative Arbeiten.",
                "workshop",
            ),
            (
                "Aktion/Workshop, Kostenlos, Lesung",
                "Buchvorstellungen am Abend",
                "Mehrere neue Bücher werden dem Publikum vorgestellt.",
                "talk",
            ),
            (
                "wein wanderung führung kultur ausstellung",
                "Turnusführung Museum",
                "Eine Führung durch das Museum.",
                "outdoor",
            ),
            (
                "lokal kultur markt fest sport natur",
                "Tennismeisterschaften der Seniorinnen und Senioren",
                "Das Turnier beginnt am Vormittag.",
                "sports",
            ),
            (
                "lokal kultur markt fest sport natur",
                "Sicher sprechen und diskutieren im Bildungsurlaub",
                "Ein fünftägiger Kurs mit praktischen Übungen.",
                "workshop",
            ),
        ]

        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(categorize_event(source_category, title, description)["key"], expected)

    def test_compound_words_do_not_trigger_forum_or_park_categories(self):
        # "Rheinforum" is a venue, not a discussion forum; "Parkbuchhandlung"
        # is a bookshop, not a park. Both used to match as bare substrings.
        cases = [
            ("kino film kultur", "Kinotag im Rheinforum", '"Das Dschungelbuch".', "cinema"),
            ("lesung literatur", "Weidle stellen »Die rastlosen Jahre« vor",
             "Veranstaltungsort: Parkbuchhandlung, Bonn-Bad Godesberg.", "talk"),
            # Genuine matches must survive.
            ("", "Forum Wissenschaft", "", "talk"),
            ("", "CULTRA x Stadtpark Ost", "", "outdoor"),
            ("", "Treffpunkt im Park", "", "outdoor"),
        ]
        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    categorize_event(source_category, title, description)["key"], expected
                )


    def test_kabarett_and_comedy_remain_in_the_stage_category(self):
        cases = [
            # A cabaret house passes its own genre; the title is just a name.
            ("kabarett kleinkunst", "Max Uthoff - uns.ich.er", "", "stage"),
            ("kabarett kleinkunst", "Wladimir Kaminer - Müttertage", "", "stage"),
            ("comedy kabarett impro", "LOL Sommer Open Air", "", "stage"),
            ("", "Kabarett-Abend", "", "stage"),
            ("", "Improtheater Bonn", "", "stage"),
            ("", "Poetry Slam", "", "stage"),
            # Drama, dance and opera stay on the stage category.
            ("", "Theaterstück Hamlet", "", "stage"),
            ("", "Tanztheater", "", "stage"),
            ("", "Oper: Carmen", "", "stage"),
            # A concert keeps winning on its own title.
            ("kabarett kleinkunst", "Konzert der Big Band", "", "concert"),
        ]
        for source_category, title, description, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    categorize_event(source_category, title, description)["key"], expected
                )


if __name__ == "__main__":
    unittest.main()
