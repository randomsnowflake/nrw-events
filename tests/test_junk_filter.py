import unittest
from datetime import datetime

from scripts.nrw_events import common


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
        self.assertFalse(common.is_junk_event(event("Tag der offenen Tür im Stadtratssaal")))


if __name__ == "__main__":
    unittest.main()
