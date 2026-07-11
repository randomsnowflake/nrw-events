import unittest
from datetime import datetime

from scripts.nrw_events import common
from scripts.nrw_events.quality import (
    QualityAction,
    evaluate_event_quality,
    summarize_event_quality,
)


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
    def test_quality_summary_exposes_longitudinal_completeness_metrics(self):
        metrics = summarize_event_quality([{
            "title": "Event", "source": "Test", "start_date": "2026-06-12",
            "end_date": "2026-06-12", "date": "2026-06-12", "city": "Bonn",
            "link": "https://example.test", "score": 1.0, "status": "scheduled",
            "timezone": "Europe/Berlin", "category_key": "other",
            "category_label": "Sonstiges", "category_confidence": 0.0,
            "category_reason": "other:no-match", "all_day": True,
            "location_confidence": "known_city", "time": "", "venue": "Bonn",
            "description": "", "price": "",
        }])

        self.assertEqual(metrics["event_count"], 1)
        self.assertEqual(sum(metrics["missing_required_fields"].values()), 0)
        self.assertEqual(metrics["uncategorized_count"], 1)
        self.assertEqual(metrics["optional_field_coverage"]["venue"], 1)

    def test_quality_decisions_are_machine_readable(self):
        decision = evaluate_event_quality({"title": "Privacy Policy"})
        self.assertEqual(decision.action, QualityAction.DROP)
        self.assertTrue(decision.rule_id)
        self.assertTrue(decision.reason)

    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 6, 12)
        common.END_DATE = datetime(2026, 6, 25)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

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

    def test_blocks_political_admin_unless_it_is_a_destination_event(self):
        self.assertTrue(common.is_junk_event(event("Fraktionssitzung der Ratsfraktion")))
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


if __name__ == "__main__":
    unittest.main()
