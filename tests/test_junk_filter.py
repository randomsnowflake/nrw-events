import unittest
from datetime import datetime

from nrw_events import common
from nrw_events.quality import (
    QualityAction,
    evaluate_event_quality,
    quality_gate_warnings,
    summarize_event_quality,
)
from tests.helpers import patch_window


def event(title, description="", category="", source="Test"):
    return {
        "title": title,
        "description": description,
        "venue": "Bonn",
        "link": "https://example.test/event",
        "date": common.TODAY.strftime("%Y-%m-%d"),
        "category": category,
        "source": source,
    }


class JunkFilterTests(unittest.TestCase):
    def test_recurring_service_reports_the_actual_longest_matches(self):
        decision = evaluate_event_quality(event(
            "Kleiderverkauf im Bürgerhaus",
            description="Jeden ersten Montag Ausgabe gespendeter Kleidung.",
        ))

        self.assertEqual(decision.rule_id, "civic.recurring-service")
        self.assertEqual(decision.matched_terms, ("jeden ersten", "kleiderverkauf"))

    def test_quality_summary_exposes_longitudinal_completeness_metrics(self):
        metrics = summarize_event_quality([{
            "title": "Event", "source": "Test", "start_date": "2026-06-12",
            "end_date": "2026-06-12", "date": "2026-06-12", "city": "Bonn",
            "link": "https://example.test", "score": 1.0, "status": "scheduled",
            "timezone": "Europe/Berlin", "category_key": "other",
            "category_label": "Sonstiges", "category_confidence": 0.0,
            "category_reason": "other:no-match", "all_day": True,
            "location_confidence": "known_city", "time": "", "venue": "Bonn",
            "venue_id": "", "venue_address": "", "description": "", "price": "",
        }])

        self.assertEqual(metrics["event_count"], 1)
        self.assertEqual(sum(metrics["missing_required_fields"].values()), 0)
        self.assertEqual(metrics["uncategorized_count"], 1)
        self.assertEqual(metrics["optional_field_coverage"]["venue"], 1)
        self.assertEqual(metrics["registered_venue_count"], 0)
        self.assertEqual(metrics["venue_address_count"], 0)
        self.assertEqual(metrics["by_source"]["Test"]["event_count"], 1)
        self.assertEqual(metrics["by_source"]["Test"]["low_confidence_count"], 1)
        self.assertEqual(metrics["by_source"]["Test"]["missing_venue_count"], 0)
        self.assertEqual(metrics["by_source"]["Test"]["unresolved_location_count"], 0)

    def test_quality_summary_separates_source_problem_rates_without_gating(self):
        rows = [
            {
                "source": "Sparse Feed", "category_confidence": 0.4,
                "location_confidence": "unresolved", "venue": "",
            },
            {
                "source": "Sparse Feed", "category_confidence": 0.8,
                "location_confidence": "known_city", "venue": "Rathaus",
            },
            {
                "source": "Healthy Feed", "category_confidence": 1.0,
                "location_confidence": "exact", "venue": "Theater",
            },
        ]

        metrics = summarize_event_quality(rows)

        self.assertEqual(metrics["event_count"], 3)
        self.assertEqual(metrics["by_source"]["Sparse Feed"], {
            "event_count": 2,
            "occurrence_count": 2,
            "work_unit_count": 2,
            "low_confidence_count": 1,
            "low_confidence_rate": 0.5,
            "unresolved_location_count": 1,
            "unresolved_location_rate": 0.5,
            "missing_venue_count": 1,
            "missing_venue_rate": 0.5,
            "registered_venue_count": 0,
            "registered_venue_rate": 0.0,
            "venue_address_count": 0,
            "venue_address_rate": 0.0,
        })
        self.assertEqual(metrics["by_source"]["Healthy Feed"]["low_confidence_rate"], 0.0)

    def test_quality_summary_distinguishes_occurrences_from_unique_series(self):
        rows = [
            {
                "source": "Linz",
                "title": "Ausstellung 11 bis 8",
                "series_id": "series-linz-exhibition",
                "start_date": f"2026-08-{day:02d}",
            }
            for day in range(1, 11)
        ] + [{
            "source": "Linz",
            "title": "Sommerkonzert",
            "series_id": "series-linz-concert",
            "start_date": "2026-08-12",
        }]

        metrics = summarize_event_quality(rows)

        self.assertEqual(metrics["occurrence_count"], 11)
        self.assertEqual(metrics["work_unit_count"], 2)
        self.assertEqual(metrics["by_source"]["Linz"]["occurrence_count"], 11)
        self.assertEqual(metrics["by_source"]["Linz"]["work_unit_count"], 2)

    def test_quality_summary_counts_a_shared_series_once_across_sources(self):
        rows = [
            {
                "source": source,
                "title": "Sommerkonzert",
                "series_id": "series-shared-concert",
                "start_date": start_date,
            }
            for source, start_date in (
                ("Official calendar", "2026-08-12"),
                ("Venue calendar", "2026-08-19"),
            )
        ]

        metrics = summarize_event_quality(rows)

        self.assertEqual(metrics["occurrence_count"], 2)
        self.assertEqual(metrics["work_unit_count"], 1)
        self.assertEqual(metrics["by_source"]["Official calendar"]["work_unit_count"], 1)
        self.assertEqual(metrics["by_source"]["Venue calendar"]["work_unit_count"], 1)

    def test_quality_decisions_are_machine_readable(self):
        decision = evaluate_event_quality({"title": "Privacy Policy"})
        self.assertEqual(decision.action, QualityAction.DROP)
        self.assertEqual(decision.rule_id, "metadata.navigation-page")
        self.assertTrue(decision.reason)

    def test_legacy_policy_groups_have_stable_named_decisions(self):
        cases = (
            (event("Fraktionssitzung der Ratsfraktion"), "civic.governance"),
            (event("Interkultureller Frauentreff"), "civic.routine-meetup"),
            (event("Wochenmarkt Bonn"), "civic.routine-market"),
            (event("Deutschkurs für Männer"), "civic.course"),
            ({**event("Static listing"), "link": "https://eventim.de/city/bonn"},
             "metadata.directory-link"),
        )

        for candidate, expected_rule in cases:
            with self.subTest(candidate=candidate):
                decision = evaluate_event_quality(candidate)
                self.assertTrue(decision.should_drop)
                self.assertEqual(decision.rule_id, expected_rule)

    def test_civic_body_credited_as_co_organizer_is_not_a_governance_meeting(self):
        """Naming a Beirat in the prose credits a host; it does not convene one.

        The governance rule reads the title, category, venue and link, never the
        free description — a public sports course announcing "gemeinsam mit dem
        Seniorenbeirat" was otherwise dropped as an administrative meeting the
        moment the source handed over its full copy.
        """
        course = event(
            "Sport im Park am Schloss",
            "Von Mai bis September lädt die Stadt gemeinsam mit dem Seniorenbeirat "
            "und dem TV Forsbach zu zwei kostenlosen Outdoor-Kursen ein. "
            "Die Teilnahme ist kostenfrei und ohne Anmeldung möglich.",
        )

        self.assertFalse(evaluate_event_quality(course).should_drop)

        for convened in (
            event("Sitzung des Seniorenbeirats", "Öffentliche Sitzung."),
            event("Ausschuss für Umwelt und Verkehr", "Öffentliche Beratung."),
            {**event("Öffentliche Beratung"), "venue": "Ratssaal, Sitzungszimmer"},
        ):
            with self.subTest(title=convened["title"], venue=convened["venue"]):
                decision = evaluate_event_quality(convened)
                self.assertTrue(decision.should_drop)
                self.assertEqual(decision.rule_id, "civic.governance")

    def test_quality_gates_warn_only_for_material_source_rates(self):
        metrics = summarize_event_quality([
            {"source": "Weak Source", "category_key": "workshop",
             "category_confidence": 0.2,
             "location_confidence": "unresolved", "venue": ""}
            for _ in range(10)
        ] + [
            {"source": "Healthy Source", "category_key": "stage",
             "category_confidence": 1.0,
             "location_confidence": "exact", "venue": "Theater"}
            for _ in range(10)
        ])
        source_results = {
            "Weak Runner": {
                "accepted_event_count": 4,
                "rejection_reasons": {"quality:civic.course": 6},
            },
            "Healthy Runner": {
                "accepted_event_count": 10,
                "rejection_reasons": {},
            },
        }

        warnings = quality_gate_warnings(metrics, source_results)

        self.assertEqual(
            {warning["rule_id"] for warning in warnings},
            {
                "quality.low-confidence-rate",
                "quality.unresolved-location-rate",
                "quality.missing-venue-rate",
                "quality.drop-rate",
            },
        )
        self.assertEqual({warning["source"] for warning in warnings},
                         {"Weak Source", "Weak Runner"})

    def test_uncategorized_gate_uses_documented_global_threshold(self):
        metrics = summarize_event_quality([
            {"source": "Mixed", "category_key": "other"}
            for _ in range(7)
        ] + [
            {"source": "Mixed", "category_key": "stage"}
            for _ in range(93)
        ])

        warnings = quality_gate_warnings(metrics, {})

        warning = next(
            item for item in warnings
            if item["rule_id"] == "quality.uncategorized-rate"
        )
        self.assertEqual(warning["source"], "all")
        self.assertEqual(warning["rate"], 0.07)
        self.assertEqual(warning["threshold"], 0.06)

    def test_advertising_markers_at_content_start_are_dropped_by_named_rule(self):
        cases = (
            {"title": "IWK Ausbildung", "description": "ANZEIGE: Jetzt bewerben"},
            {"title": " Advertorial – Neues aus der Region", "description": ""},
            {"title": "Sommerfest", "description": "\n  Sponsored: Präsentiert von Acme"},
        )

        for candidate in cases:
            with self.subTest(candidate=candidate):
                decision = evaluate_event_quality(candidate)
                self.assertTrue(decision.should_drop)
                self.assertEqual(decision.rule_id, "editorial.advertising-marker")
                self.assertTrue(decision.matched_terms)

    def test_advertising_words_away_from_content_start_do_not_trigger_marker_rule(self):
        decision = evaluate_event_quality({
            "title": "Diskussion über Advertorials",
            "description": "Eine Führung mit anschließender Diskussion über Sponsored Content.",
        })

        self.assertNotEqual(decision.rule_id, "editorial.advertising-marker")

    def test_explicit_unavailable_status_markers_are_dropped_by_named_rule(self):
        cases = (
            {"title": "AUSGEBUCHT - Naturfreunde Ferienwochen", "description": ""},
            {"title": "Ferienprogramm", "description": "- AUSGEBUCHT - Noch nichts vor?"},
            {"title": "Belcanto", "description": "Konzert – Ausverkauft"},
            {
                "title": "Platz & Prost",
                "description": "08. August 2026 Platz & Prost Summer Edition 2026 abgesagt",
            },
            {"title": "GESCHLOSSEN – Workshop im Museum", "description": ""},
            {"title": "Familienführung", "description": "Die Anmeldung ist geschlossen."},
            {"title": "Lesung", "description": "Keine Tickets mehr verfügbar."},
        )

        for candidate in cases:
            with self.subTest(candidate=candidate):
                decision = evaluate_event_quality(candidate)
                self.assertTrue(decision.should_drop)
                self.assertEqual(decision.rule_id, "availability.unavailable")
                self.assertTrue(decision.matched_terms)

    def test_contextual_status_words_do_not_hide_available_events(self):
        cases = (
            {
                "title": "Nachtwache",
                "description": "Was passiert, wenn die Türen geschlossen sind? Dann beginnt das Escape Game.",
            },
            {
                "title": "Rokoko under Construction",
                "description": "Augustusburg 14.-18.07. geschlossen. Die Ausstellung läuft anschließend weiter.",
            },
            {
                "title": 'Eitorf "live" mit DOSENMILCH',
                "description": "Das Benefizkonzert im April war bereits ausverkauft. Nun folgt das neue Konzert.",
            },
            {
                "title": "Sommerfestival",
                "description": "Das Festival läuft weiter; viele Termine sind bereits ausverkauft",
            },
            {
                "title": "Summerclosing auf dem Rhein",
                "description": "Die Party ist im Vorverkauf ausverkauft! Es gibt nur noch Standby-Tickets.",
            },
            {
                "title": "Konzert am neuen Termin",
                "description": "Der Termin wurde vom Mai hierher verlegt",
            },
            {
                "title": "Südstadt",
                "description": "Die Südstadt ist ein in sich geschlossenes Areal gründerzeitlichen Bauens.",
            },
            {
                "title": "Flohmarkt",
                "description": "Neuwaren und Lebensmittel sind ausgeschlossen.",
            },
            {
                "title": "Ausstellung: Abgesagte Pläne der Stadtgeschichte",
                "description": "Historische Ausstellung.",
            },
        )

        for candidate in cases:
            with self.subTest(candidate=candidate):
                decision = evaluate_event_quality(candidate)
                self.assertFalse(decision.should_drop)

    def test_shift_markers_are_not_unavailability_drops(self):
        for title in ("Theaterabend verschoben", "Konzert verlegt"):
            with self.subTest(title=title):
                decision = evaluate_event_quality({"title": title, "description": "Neuer Termin folgt"})
                self.assertFalse(decision.should_drop)

    def test_verlegt_requires_schedule_context(self):
        title = "Fliesen-Workshop"
        description = "In diesem Workshop werden Fliesen fachgerecht verlegt."

        self.assertEqual(common.event_status(title, description), "scheduled")
        self.assertFalse(evaluate_event_quality({"title": title, "description": description}).should_drop)

    def test_full_sold_out_sentence_is_unavailable(self):
        for description in (
            "Die Veranstaltung ist ausverkauft.",
            "Der Workshop ist ausgebucht.",
        ):
            with self.subTest(description=description):
                decision = evaluate_event_quality({"title": "Termin", "description": description})
                self.assertTrue(decision.should_drop)
                self.assertEqual(decision.rule_id, "availability.unavailable")

    def setUp(self):
        patch_window(self, datetime(2026, 6, 12), datetime(2026, 6, 25))

    def test_blocks_recurring_community_and_basic_course_formats(self):
        blocked_titles = [
            "Interkultureller Frauentreff",
            "Handarbeitstreff Em Ahle Kluster",
            "Seniorencafe in Siegburg-Kaldauen",
            "Gedächtnistraining",
            "Deutschkurs für Männer",
            "Pilates-Training",
            "NEU! Sitzgymnastik",
            "Rückbildungsgymnastik mit Babybetreuung",
            "Patientenveranstaltung: Behandlungsmöglichkeiten bei Darmkrebs",
            "Offene Sprechstunde im Bürgerzentrum",
            "Frühstückszeit Em Ahle Kluster",
            "Offener Puzzle-Treff",
            "Häkel-Treff",
            "Stricken und Klönen",
            "Spielezeit",
            "Treffen der Bad Honnefer Funkamateure",
            "Veranstaltung der Senioreninformation",
            "Ganzheitliche Wirbelsäulengymnastik mit Tiefenentspannung",
            "English Club am Vormittag B1-B2",
            "Klaaferei – Café Winterscheid",
            "Straßenreinigung",
            "Venen Aktionstage in der Bröltal Apotheke",
        ]

        for title in blocked_titles:
            with self.subTest(title=title):
                self.assertTrue(common.is_junk_event(event(title)))

    def test_keeps_destination_events_with_overlap_words(self):
        allowed_titles = [
            "Repair Café Bonn-Beuel",
            "18. Biker-Treffen der Biker in der Bundespolizei Sankt Augustin",
            "Tag der offenen Tür in der Kläranlage Müllekoven",
            "Yoga-Stile zum Kennenlernen - Ein Tag zum Entspannen und Auftanken",
            "Fahrradexkursion durch das Klimaviertel Bonn",
        ]

        for title in allowed_titles:
            with self.subTest(title=title):
                self.assertFalse(common.is_junk_event(event(title)))

    def test_recurring_wording_does_not_hide_named_destination_activities(self):
        cases = [
            (
                "Wachtberger Repair Café",
                "Wiederkehrender Termin: Ehrenamtliche helfen beim Reparieren.",
            ),
            (
                "ADFC-Feierabendtour",
                "Die wöchentlichen Radtouren führen rund 40 km durch die Region.",
            ),
            (
                "Kleine Auszeit für Frauen mit Kristallklangschalenreise",
                "Nicht Teil unserer regelmäßigen Gruppe; ein einmaliger Abend am 21. August.",
            ),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                self.assertFalse(common.is_junk_event({
                    **event(title, description=description),
                    "link": "https://example.test/wiederkehrende-termine/activity/",
                }))

    def test_routine_meetups_stay_blocked_beside_destination_exceptions(self):
        cases = [
            ("Treffen des ZWAR-Netzwerkes", "Wir treffen uns regelmäßig alle 14 Tage."),
            ("Selbsthilfegruppe Tag Eins", "Regelmäßige Gesprächsrunden."),
            ("Offener Näh- und Handarbeitstreff", "Wiederkehrender Termin jeden Montag."),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                self.assertTrue(common.is_junk_event({
                    **event(title, description=description),
                    "link": "https://example.test/wiederkehrende-termine/meeting/",
                }))

    def test_weak_civic_words_do_not_hide_one_off_public_events(self):
        cases = [
            (
                "Herbst-Weinprobe im Wachtberger Weinladen, RaibleWein",
                "Herbstliche Weine mit persönlicher Beratung im Weinladen.",
                "Genuss Weinprobe",
            ),
            (
                "Repair Café Lannesdorf",
                "Ehrenamtliche bieten Hilfe und persönliche Beratung beim Reparieren.",
                "Nachhaltigkeit",
            ),
            (
                "REPAIR-CAFÈ an der Mühlenbachhalle",
                "Der regelmäßig angebotene Termin lädt zum gemeinsamen Reparieren ein.",
                "Nachbarschaft",
            ),
            (
                "Brettspielabend: Bonn spielt",
                "Die Künstlerbiografie erwähnt Politikberatung; heute werden Spiele vorgestellt.",
                "Kultur",
            ),
            (
                "Empowerment für Frauen & Mädchen",
                "Ein einmaliger Workshop; weitere Angebote finden regelmäßig statt.",
                "Workshop",
            ),
            (
                "Müllsammelaktion",
                "Ein öffentlicher Aktionstag; der Verein trifft sich regelmäßig.",
                "Umwelt Aktionstag",
            ),
            (
                "Jazz Jam",
                "Die wiederkehrende Reihe präsentiert heute ein Live-Konzert.",
                "Musik Konzert",
            ),
        ]

        for title, description, category in cases:
            with self.subTest(title=title):
                self.assertFalse(common.is_junk_event(event(
                    title, description=description, category=category,
                )))

    def test_weak_civic_words_still_require_event_scoped_service_evidence(self):
        cases = [
            (
                "Individuelles Radreisen – Beratung für Mitglieder",
                "Die Beratung findet regelmäßig jeden ersten Donnerstag statt.",
                "Beratung",
                "civic.course",
            ),
            (
                "Offener Treff im Nachbarschaftshaus",
                "Der Treff findet wöchentlich statt.",
                "Soziales",
                "civic.routine-meetup",
            ),
            (
                "Schuldner- und Insolvenzberatung",
                "Im Städtischen Beratungszentrum Älterwerden, Behinderung und Rente.",
                "Kommunal",
                "civic.course",
            ),
            (
                "Advanced Embodied Contemporary Dance Training",
                "Dieses Format bietet regelmäßigen Unterricht für fortgeschrittene Tanzschaffende.",
                "Workshop",
                "civic.course",
            ),
            (
                "MittwochsTreff im Gereonshof",
                "Jeden Mittwoch findet ein Treff zum Sprachelernen statt.",
                "Begegnung",
                "civic.routine-meetup",
            ),
            (
                "Jazz Jam fällt aus",
                "Das Konzert ist abgesagt.",
                "Musik Konzert",
                "availability.unavailable",
            ),
        ]

        for title, description, category, expected_rule in cases:
            with self.subTest(title=title):
                decision = evaluate_event_quality(event(
                    title, description=description, category=category,
                ))
                self.assertTrue(decision.should_drop)
                self.assertEqual(decision.rule_id, expected_rule)

    def test_recurring_destination_markets_survive_routine_filter(self):
        cases = [
            ("Flohmarkt Bonn Siemensstraße", "Wöchentlich jeden Samstag"),
            ("Trödelmarkt Bonn Siemensstraße", "Wöchentlich jeden Samstag"),
            ("Troedelmarkt Bonn Siemensstraße", "Regelmäßig jeden Samstag"),
            ("Antikmarkt Bonn", "Wöchentlich auf dem Marktplatz"),
            ("Hofflohmarkt Bonn", "Regelmäßig in der Nachbarschaft"),
            ("Nachbarschaftsmarkt Südstadt", "Jeden ersten Sonntag mit privaten Ständen"),
            ("Feierabendmarkt - meet&eat", "Jeden ersten Donnerstag mit wechselnden Ständen"),
            ("Vieh- und Krammarkt", "Alle zwei Wochen auf dem Marktplatz"),
            (
                "Wochenmarkt-Spezial mit Abendflohmarkt",
                "Sonderveranstaltung nach dem gewöhnlichen Wochenmarkt",
            ),
            ("Samstags wöchentlicher Trödelmarkt", "Konkreter Markttermin mit privaten Ständen"),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                self.assertFalse(common.is_junk_event(event(
                    title,
                    description=description,
                    category="markt",
                )))

    def test_recurring_routine_markets_and_sales_remain_blocked(self):
        cases = [
            ("Wochenmarkt Bonn", "Wöchentlich mit Obst, Gemüse und Frischewaren"),
            ("Frischemarkt Bonn", "Regelmäßig regionale Lebensmittel"),
            ("Kleiderausgabe", "Jeden Donnerstag Verkauf gespendeter Kleidung"),
            (
                "Markt-Shop Bonn",
                "Wöchentlich geöffnet. Öffnungszeiten und unser Sortiment im Laden.",
            ),
            ("Markt am Rathaus", "Wöchentlich wiederkehrender Markt"),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                self.assertTrue(common.is_junk_event(event(
                    title,
                    description=description,
                    category="markt",
                )))

    def test_search_gate_accepts_dated_destination_markets_but_not_static_shops(self):
        for title in (
            "Flohmarkt Bonn am Samstag 15. August 2026",
            "Trödelmarkt Bonn am 15.08.2026",
            "Troedelmarkt Bonn am Sonntag 16. August 2026",
            "Antikmarkt Bonn am 16.08.2026",
        ):
            with self.subTest(title=title):
                self.assertFalse(common.is_junk_event(event(
                    title,
                    description="Konkreter Termin in Bonn",
                    category="markt",
                    source="Exa Search",
                )))

        self.assertTrue(common.is_junk_event(event(
            "Antikmarkt-Shop Bonn",
            description="Öffnungszeiten und unser Sortiment",
            category="markt",
            source="Grok Search",
        )))

    def test_keeps_french_music_descriptions_out_of_language_course_filter(self):
        self.assertFalse(common.is_junk_event(event(
            "Mirecourtplatz-Konzert",
            description="Mitsingkonzert mit französischen Chansons und kölschen Hits.",
            category="Musik/Konzert",
        )))

    def test_blocks_political_admin_unless_it_is_a_destination_event(self):
        self.assertTrue(common.is_junk_event(event("Fraktionssitzung der Ratsfraktion")))
        self.assertTrue(common.is_junk_event(event("Telefon-Hotline Bürgermeister")))
        self.assertTrue(common.is_junk_event(event("Wahlkampf-Infostand am Marktplatz")))
        self.assertTrue(common.is_junk_event(event("Rat (öffentliche Sitzung)", category="Konzert")))
        self.assertTrue(common.is_junk_event(event("Verwaltungsrat GKU", category="Konzert")))
        self.assertTrue(common.is_junk_event({
            **event("Ratssitzung im Ratssaal"),
            "venue": "Stadtmuseum Bonn",
            "link": "https://example.test/museum/ratssitzung",
        }))
        self.assertFalse(common.is_junk_event(event("Tag der offenen Tür im Stadtratssaal")))
        self.assertFalse(common.is_junk_event(event(
            "Ausstellung: Geschichte des Stadtrats",
            description="Museumsausstellung über Ratssitzung und Stadtverordnete",
            category="Ausstellung Museum",
        )))
        self.assertFalse(common.is_junk_event(event(
            "Ratssitzung im Wandel der Zeit",
            description="Sonderführung durch das Museum zur Geschichte kommunaler Politik",
            category="Museum",
        )))

    def test_keeps_cultural_stammtisch_events(self):
        self.assertTrue(common.is_junk_event(event("Offener Stammtisch im Bürgerzentrum")))
        self.assertFalse(common.is_junk_event(event(
            "Literarischer Stammtisch mit Lesung",
            description="Lesung und Gespräch im Literaturhaus",
            category="Lesung",
        )))

    def test_blocks_abi_and_graduation_balls(self):
        blocked_titles = [
            "Abiball Helmholtz Gymnasium",
            "Abi-Ball Europa Schule",
            "Abschlussball der Stufe Q2",
        ]

        for title in blocked_titles:
            with self.subTest(title=title):
                self.assertTrue(common.is_junk_event(event(title, category="Ball/Abiball")))

    def test_blocks_low_value_civic_services_by_general_content_shape(self):
        cases = [
            event(
                "Franz-Geuer-Straße in Köln-Ehrenfeld",
                description="Informieren Sie sich über die Planung und geben Sie eine Stellungnahme ab.",
            ),
            event("Blutspende III/2026"),
            event("Klimatreff und offenes Plenum"),
            event(
                "Verkauf im Kleiderpavillon",
                description="Das Team öffnet jeden Donnerstag zum Verkauf gespendeter Sachen.",
            ),
        ]

        for candidate in cases:
            with self.subTest(title=candidate["title"]):
                decision = evaluate_event_quality(candidate)
                self.assertTrue(decision.should_drop)
                self.assertNotEqual(decision.rule_id, "legacy.editorial-policy")


    def test_standing_reading_circles_are_dropped(self):
        for title in (
            "Literaturkreis Neubrück", "Lesekreis in Sülz", "Ehrenfelder Lesekreis",
            "Swisttaler Lesekreis", "Lieblingsbücher – Lesezirkel in der Bücherbrücke",
        ):
            decision = evaluate_event_quality({"title": title, "venue": "Stadtteilbibliothek"})
            with self.subTest(title=title):
                self.assertTrue(decision.should_drop)
                self.assertEqual(decision.rule_id, "civic.reading-circle")

    def test_reading_circles_naming_the_discussed_work_are_kept(self):
        decision = evaluate_event_quality({
            "title": "LESEZIRKEL DAVID SZALAY »WAS NICHT GESAGT WERDEN KANN«",
            "venue": "The Art of Books",
        })

        self.assertFalse(decision.should_drop)

    def test_online_only_sessions_are_dropped(self):
        for venue in ("Zoom", "Zoom (Der Link wird am Tag der Veranstaltung veröffentlicht.)",
                      "MS Teams", "online"):
            decision = evaluate_event_quality({"title": "Info Wohnraum", "venue": venue})
            with self.subTest(venue=venue):
                self.assertTrue(decision.should_drop)
                self.assertEqual(decision.rule_id, "civic.online-only")

    def test_physical_venues_are_not_mistaken_for_platforms(self):
        # An open-air cinema named ZOOM and a venue that merely mentions a
        # stream both keep a real address to visit.
        for title, venue in (("ZOOM OPEN AIR 26", "Brühl"),
                             ("Lesung", "Zoom Kulturhaus Bonn")):
            decision = evaluate_event_quality({"title": title, "venue": venue})
            with self.subTest(venue=venue):
                self.assertFalse(decision.should_drop)


if __name__ == "__main__":
    unittest.main()
