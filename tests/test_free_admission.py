import unittest

from nrw_events.common import infer_free_admission_price


class FreeAdmissionDetectionTests(unittest.TestCase):
    def test_detects_explicit_whole_event_free_admission_phrases(self):
        cases = [
            ("Sommerfestival", "Der Eintritt ist frei.", ""),
            ("Sommerfestival", "Der Eintritt ist nach wie vor frei!", ""),
            ("Schützenfest", "Der Eintritt ist zu allen Veranstaltungen frei.", ""),
            ("GA-Sommergarten", "Live-Musik bei freiem Eintritt.", ""),
            ("Fahrradtour", "Eine kostenlose, geführte Fahrradtour durch Troisdorf.", ""),
            ("Auf ein Buch", "Treffen in der Stadtbibliothek. Kostenlos und unverbindlich.", ""),
            ("Switch 2 zocken", "Anmeldung erforderlich.kostenfreiab 6 Jahren", ""),
            ("Kostenlos Seepferdchen-Prüfung", "", ""),
            ("Offener Kunstraum", "", "Eintritt: 0 €"),
            ("Musik im Park", "", "frei, Hutspenden erbeten"),
        ]

        for title, description, price in cases:
            with self.subTest(title=title, description=description, price=price):
                self.assertEqual(
                    infer_free_admission_price(title, description, price),
                    "kostenlos",
                )

    def test_rejects_limited_or_unrelated_free_signals(self):
        cases = [
            ("Vorlesen", "Kostenloser Bibliotheksausweis erforderlich.", ""),
            ("Sommerleseclub", "Zum Abschluss gibt es gratis Popcorn.", ""),
            ("Repair Café", "Kleidungsstücke können kostenlos geändert werden.", ""),
            ("Wanderung", "Kurze Anmeldung (kostenlos) bis zum Vorabend.", ""),
            ("Führung", "Der Eintritt in den Park ist frei. Die Führung kostet 8 Euro.", ""),
            ("Familienmuseum", "Eintritt 12 Euro, Kinder bis 6 Jahre kostenlos.", ""),
            ("Konzert", "Der Eintritt ist nicht frei.", ""),
        ]

        for title, description, price in cases:
            with self.subTest(title=title, description=description, price=price):
                self.assertEqual(infer_free_admission_price(title, description, price), "")


if __name__ == "__main__":
    unittest.main()
