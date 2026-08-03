import unittest

from nrw_events import common
from nrw_events.validation import canonicalize_event

infer_free_admission_price = common.infer_free_admission_price


class FreeAdmissionDetectionTests(unittest.TestCase):
    def test_detects_explicit_whole_event_free_admission_phrases(self):
        cases = [
            ("Sommerfestival", "Der Eintritt ist frei.", ""),
            ("Sommerfestival", "Der Eintritt ist nach wie vor frei!", ""),
            ("Siegburger Sommer Live", "Der Eintritt ist wie immer kostenlos.", ""),
            ("Schützenfest", "Der Eintritt ist zu allen Veranstaltungen frei.", ""),
            ("GA-Sommergarten", "Live-Musik bei freiem Eintritt.", ""),
            ("Fahrradtour", "Eine kostenlose, geführte Fahrradtour durch Troisdorf.", ""),
            ("Auf ein Buch", "Treffen in der Stadtbibliothek. Kostenlos und unverbindlich.", ""),
            ("Sport im Park", "Kostenlos und draußen – ohne Anmeldung.", ""),
            ("Ferienspass: Bogenschießen", "Kosten: kostenfrei. Keine Anmeldung erforderlich.", ""),
            ("Vernissage", "Einlass 16:30 UhrEintritt frei!", ""),
            ("Switch 2 zocken", "Anmeldung erforderlich.kostenfreiab 6 Jahren", ""),
            ("Kostenlos Seepferdchen-Prüfung", "", ""),
            ("Offener Kunstraum", "", "Eintritt: 0 €"),
            ("Musik im Park", "", "frei, Hutspenden erbeten"),
            # Calendar templates that append their currency to every ticket
            # label, whatever the maintained value is (KULT41).
            ("Klimatreff", "", "Eintritt: frei€"),
            ("Ausstellung", "", "Eintritt: frei 0 €"),
            ("Lesung", "", "Eintritt frei €"),
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
            ("Familientag", "Kosten: kostenfrei für Kinder. Erwachsene zahlen 8 Euro.", ""),
            ("Sportkurs", "Kostenlos und draußen für Mitglieder; Gäste zahlen 5 Euro.", ""),
            ("Konzert", "Der Eintritt ist nicht frei.", ""),
            # "frei" as the start of a longer word must not read as free.
            ("Markt", "", "Eintritt: freitags 10 €"),
        ]

        for title, description, price in cases:
            with self.subTest(title=title, description=description, price=price):
                self.assertEqual(infer_free_admission_price(title, description, price), "")

    def test_infers_free_visitor_access_for_safe_public_event_types(self):
        cases = [
            ("Flohmarkt Kölner Altstadt", "Standpreis: 15 Euro pro laufendem Meter"),
            ("Hofflohmarkt Rondorf", "Hausanwohner verkaufen in ihren Höfen."),
            ("Antik- und Trödelmarkt Bad Godesberg", "Viele Verkaufsstände in der Innenstadt."),
            ("Poppelsdorfer Straßenfest", "Vereine und Gastronomie feiern im Viertel."),
            ("Tag der offenen Tür", "Blicke hinter die Kulissen."),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                self.assertEqual(infer_free_admission_price(title, description), "kostenlos")

    def test_does_not_infer_free_access_for_ticketed_or_ambiguous_markets(self):
        cases = [
            ("Nachtflohmarkt", "Tickets im Vorverkauf.", ""),
            ("Indoor-Flohmarkt", "In der Stadthalle.", ""),
            ("Flohmarkt Spezial", "Besuchereintritt 4 Euro.", ""),
            ("Flohmarkt Spezial", "", "4 Euro"),
            ("Designmarkt", "Lokale Labels und Kunsthandwerk.", ""),
            ("St. Pantaleon Kirmes", "Traditionelles Kirmesprogramm.", ""),
        ]

        for title, description, price in cases:
            with self.subTest(title=title):
                self.assertEqual(infer_free_admission_price(title, description, price), "")

    def test_requires_explicit_free_evidence_for_kirmes(self):
        self.assertEqual(
            infer_free_admission_price(
                "Anna-Kirmes in Alfter",
                "Der Eintritt auch zu den Konzerten ist frei.",
            ),
            "kostenlos",
        )

    def test_transport_and_venue_metadata_are_not_free_admission_evidence(self):
        event = common.make_event(
            "Abendkonzert",
            common.TODAY,
            None,
            "Eintritt frei Kulturzentrum",
            "Bonn",
            "Live-Musik am Abend.",
            "https://example.test/eintritt-frei/abendkonzert",
            "Kostenlose Veranstaltungen Bonn",
            "konzert",
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["price"], "")
        self.assertEqual(canonicalize_event(event).price, "")


if __name__ == "__main__":
    unittest.main()
