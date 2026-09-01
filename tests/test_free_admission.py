import unittest

from nrw_events import common
from nrw_events.validation import canonicalize_event


infer_free_admission_price = common.infer_free_admission_price


class FreeAdmissionDetectionTests(unittest.TestCase):
    def test_explicit_source_wording_keeps_explicit_provenance(self):
        cases = [
            ("Lesung", "Der Eintritt ist frei."),
            ("Konzert", "Musik am Rhein.\n\nfrei, es geht der Hut rum"),
            ("Workshop", "Der Workshop ist kostenlos und ohne Anmeldung."),
            ("Radtour", "Eine kostenlose, geführte Fahrradtour durch Troisdorf."),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                event = common.make_event(
                    title, common.TODAY, None, "Rathaus", "Brühl", description,
                    "https://example.test/event", "SiteKit regional", "lokal",
                    source_id="sitekit-bruehl",
                )
                self.assertIsNotNone(event)
                assert event is not None
                self.assertEqual(event["price"], "kostenlos")
                self.assertEqual(canonicalize_event(event).admission_basis, "explicit")

    def test_explicit_provenance_survives_canonicalization(self):
        event = common.make_event(
            "Konzert im Park", common.TODAY, None, "Stadtpark", "Bonn",
            "Eintritt frei; um eine Spende wird gebeten.",
            "https://example.test/konzert", "SiteKit regional", "konzert",
            source_id="sitekit-bruehl",
        )

        self.assertIsNotNone(event)
        assert event is not None
        canonical = canonicalize_event(event)
        self.assertEqual(canonical.admission_basis, "explicit")
        self.assertTrue(canonical.admission["isFree"])
        self.assertEqual(canonical.admission["basis"], "structured")

    def test_pre_truncation_sources_record_explicit_basis_in_raw_event(self):
        for source, source_id in (
            ("Haus der Geschichte", "haus-der-geschichte"),
            ("Literaturhaus Bonn", "literaturhaus-bonn"),
        ):
            with self.subTest(source=source):
                event = common.make_event(
                    "Lesung", common.TODAY, None, "Museum", "Bonn",
                    "Langer redaktioneller Text. Der Eintritt ist frei.",
                    "https://example.test/lesung", source, "literatur",
                    source_id=source_id,
                )
                self.assertIsNotNone(event)
                assert event is not None
                self.assertEqual(event["admission_basis"], "explicit")

    def test_non_admission_controls_do_not_gain_explicit_provenance(self):
        cases = [
            ("Flohmarkt", "Stände im ganzen Viertel."),
            ("Markt", "Standgebühr für Verkäufer: 10 Euro."),
            ("Repair Café", "Spenden für Ersatzteile sind willkommen."),
            ("Lesung", "Autorengespräch in der Stadtbibliothek."),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                _price, basis = common.infer_admission(title, description)
                self.assertNotEqual(basis, "explicit")
                event = common.make_event(
                    title, common.TODAY, None, "Marktplatz", "Bonn", description,
                    "https://example.test/event", "Quelle", "lokal",
                )
                self.assertIsNotNone(event)
                assert event is not None
                self.assertIsNone(canonicalize_event(event).admission["isFree"])

    def test_paid_museum_admission_wins_over_a_free_activity(self):
        paid_phrases = (
            "Zu zahlen ist der Museumseintritt.",
            "Der reguläre Eintritt ins Museum ist zu entrichten.",
            "Zuzüglich ist der Eintritt ins Museum zu zahlen.",
            "Es gilt der reguläre Museumseintritt.",
            "Der Museumseintritt fällt zusätzlich an.",
            "Kostenlos, zzgl. Eintritt in das Museum.",
            "Zuzüglich Museumseintritt.",
            "Der Museumseintritt muss bezahlt werden.",
            "Der Museumseintritt ist zu bezahlen.",
            "Der Museumseintritt muss entrichtet werden.",
            "Der reguläre Museumseintritt wird erhoben.",
        )
        for paid_phrase in paid_phrases:
            with self.subTest(paid_phrase=paid_phrase):
                description = f"Die Führung ist kostenlos. {paid_phrase}"
                self.assertEqual(
                    common.infer_admission("Museumsführung", description),
                    ("kostenpflichtig", "explicit"),
                )
                event = common.make_event(
                    "Museumsführung", common.TODAY, None, "Museum", "Troisdorf",
                    description, "https://example.test/museum", "Troisdorf", "führung",
                    source_id="troisdorf",
                )
                self.assertIsNotNone(event)
                assert event is not None
                canonical = canonicalize_event(event)
                self.assertFalse(canonical.admission["isFree"])
                self.assertEqual(canonical.admission_basis, "explicit")

    def test_negated_museum_admission_is_not_classified_paid(self):
        phrases = (
            "Kein Museumseintritt erforderlich.",
            "Es ist kein zusätzlicher Museumseintritt erforderlich.",
            "Museumseintritt ist nicht erforderlich.",
            "Der Museumseintritt ist nicht zu zahlen.",
            "Der Museumseintritt ist nicht mehr erforderlich.",
            "Der Eintritt ins Museum ist keineswegs zu zahlen.",
            "Der Museumseintritt ist keinesfalls erforderlich.",
            "Weder der Museumseintritt noch die Führung sind zu zahlen.",
            "Der Museumseintritt ist nie zu zahlen.",
            "Der Museumseintritt ist unter keinen Umständen zu zahlen.",
            "Nicht zu zahlen ist der Museumseintritt.",
            "Keinesfalls zu zahlen ist der Museumseintritt.",
            "Nie zu zahlen ist der Museumseintritt.",
            "Unter keinen Umständen zu zahlen ist der Museumseintritt.",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                description = f"Die Führung ist kostenlos. {phrase}"
                price, basis = common.infer_admission("Museumsführung", description)
                self.assertNotEqual((price, basis), ("kostenpflichtig", "explicit"))
                event = common.make_event(
                    "Museumsführung", common.TODAY, None, "Museum", "Troisdorf",
                    description, "https://example.test/museum", "Troisdorf", "führung",
                    source_id="troisdorf",
                )
                self.assertIsNotNone(event)
                assert event is not None
                self.assertTrue(canonicalize_event(event).admission["isFree"])

    def test_unrelated_negation_does_not_hide_paid_museum_admission(self):
        descriptions = (
            "Die Führung ist nie kostenlos. Der Museumseintritt ist zu bezahlen.",
            "Unter keinen Umständen ist die Führung kostenlos; der Museumseintritt wird erhoben.",
            "Weder Audioguide noch Garderobe sind kostenlos. Der Museumseintritt muss entrichtet werden.",
        )
        for description in descriptions:
            with self.subTest(description=description):
                event = common.make_event(
                    "Museumsführung", common.TODAY, None, "Museum", "Troisdorf",
                    description, "https://example.test/museum", "Troisdorf", "führung",
                    source_id="troisdorf",
                )
                self.assertIsNotNone(event)
                assert event is not None
                canonical = canonicalize_event(event)
                self.assertFalse(canonical.admission["isFree"])
                self.assertEqual(canonical.admission_basis, "explicit")

    def test_ancillary_free_access_does_not_become_admission_evidence(self):
        phrases = (
            "Der Zugang zum Livestream ist kostenlos.",
            "Der Zugang zum Download ist kostenlos.",
            "Der Besuch der Website ist kostenlos.",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertFalse(common.has_explicit_free_admission_wording(
                    "Museumsführung", phrase,
                ))

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
            ("Bibliotheksangebot", "", "Die Teilnahme ist kostenlos."),
            ("Altstadtfest", "", "Eintritt frei !"),
            ("Kulturnacht", "", "Kostenfrei!"),
            ("Botanischer Rundgang", "", "Die Führung ist kostenlos. Spenden sind willkommen."),
            ("Ausstellung", "", "kostenloser Eintritt"),
            ("Ausstellung", "", "Die Ausstellung ist kostenlos."),
            ("Sommerferienaktion", "", "Das Ferienprogramm ist kostenlos."),
            ("Vesper", "Der Eintirtt ist frei - Spenden sind herzlich erbeten.", ""),
            ("Sommerfestival", "Der Eintritt ist natürlich wieder frei.", ""),
            ("Pedelec-Training", "Das kostenlose Pedelec-Training vermittelt eine sichere Fahrweise.", ""),
            ("Tribute-Konzert", "Hier könnt ihr euch die größten Hits kostenlos anhören.", ""),
            ("Singtreff", "Gemeinsam singen.\n\nkostenlos", ""),
            ("Offener Treff", "Kostenlos natürlich.", ""),
            ("Wissens-Olympiade", "Anmeldung erforderlich.\n\nKostenfrei!\n\nTeste dein Wissen.", ""),
            ("Ausstellung", "Öffnungszeiten am Wochenende.\n\nfrei", ""),
            ("Konzert", "Pop, Jazz und Latin.\n\nfrei, es geht der Hut rum", ""),
            ("Ausstellung", "Öffnungszeiten am Wochenende.<p>frei</p>", ""),
            ("Videokunst", "Einlass gratis", ""),
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
            ("Textilwerkstatt", "Kleidungsstücke können kostenlos geändert werden.", ""),
            ("Wanderung", "Kurze Anmeldung (kostenlos) bis zum Vorabend.", ""),
            ("Führung", "Der Eintritt in den Park ist frei. Die Führung kostet 8 Euro.", ""),
            ("Familienmuseum", "Eintritt 12 Euro, Kinder bis 6 Jahre kostenlos.", ""),
            ("Familientag", "Kosten: kostenfrei für Kinder. Erwachsene zahlen 8 Euro.", ""),
            ("Sportkurs", "Kostenlos und draußen für Mitglieder; Gäste zahlen 5 Euro.", ""),
            ("Konzert", "Der Eintritt ist nicht frei.", ""),
            ("Ausstellung", "Einlass 15 Uhr. Anmeldung nicht erforderlich: frei", ""),
            ("Fitnesskurs", "Die erste Stunde ist ein kostenloses Probetraining.", ""),
            ("Museum", "Der Audioguide kann kostenlos heruntergeladen werden.", ""),
            ("Ausstellung", "Eintritt 10 Euro.\n\nfrei", ""),
            ("Workshop", "Kostenfrei!\n\nTeilnahmegebühr 5 Euro.", ""),
            ("Kinderkurs", "Kinder können kostenlos teilnehmen.", ""),
            (
                "Stadtführung",
                "Begleitpersonen von Menschen mit Beeinträchtigungen nehmen kostenlos teil.",
                "",
            ),
            ("Vereinsabend", "Mitglieder können kostenlos mitmachen.", ""),
            ("Jugendtreff", "Jugendlichen wird kostenloser Eintritt gewährt.", ""),
            ("Vereinsabend", "Mitgliedern wird kostenloser Eintritt gewährt.", ""),
            ("Museum", "Kostenlos! Audioguide herunterladen.", ""),
            ("Vereinsabend", "Kostenfrei. Nur für Mitglieder.", ""),
            # "frei" as the start of a longer word must not read as free.
            ("Markt", "", "Eintritt: freitags 10 €"),
            ("Museumsführung", "", "Eintritt 3 Euro, Führung kostenlos"),
            ("Tanzparty", "", "Tanzparty: 5 €; Mitglieder kostenlos"),
            ("Familienmuseum", "", "5 Euro (Kinder bis 12 Jahre kostenfrei)"),
            (
                "#IFEELYOU - Dimensionen der Empathie",
                "Freier Eintritt am Eröffnungsabend sowie an jedem ersten Sonntag im Monat. "
                "Kinder und Jugendliche bis einschließlich 18 Jahre haben immer freien Eintritt.",
                "kostenlos",
            ),
            (
                "Aki Inomata: Mit-werden",
                "Freier Eintritt für alle an jedem ersten Sonntag im Monat. "
                "Kinder und Jugendliche bis einschließlich 18 Jahre haben immer freien Eintritt.",
                "kostenlos",
            ),
            (
                "Parkführung",
                "",
                "Der Eintritt in den Park ist frei. Kosten für die Führung: Erwachsene 8 Euro",
            ),
        ]

        for title, description, price in cases:
            with self.subTest(title=title, description=description, price=price):
                self.assertEqual(infer_free_admission_price(title, description, price), "")

    def test_unqualified_free_admission_still_wins_beside_a_child_discount(self):
        self.assertEqual(
            infer_free_admission_price(
                "Familientag",
                "Der Eintritt ist frei. Kinder und Jugendliche bis 18 Jahre haben freien Eintritt.",
                "",
            ),
            "kostenlos",
        )

    def test_infers_free_visitor_access_for_safe_public_event_types(self):
        cases = [
            ("Hofflohmarkt Rondorf", "Hausanwohner verkaufen in ihren Höfen."),
            ("Antik- und Trödelmarkt Bad Godesberg", "Viele Verkaufsstände in der Innenstadt."),
            ("Poppelsdorfer Straßenfest", "Vereine und Gastronomie feiern im Viertel."),
            ("Tag der offenen Tür", "Blicke hinter die Kulissen."),
            ("Wachtberger Repair Café", "Gemeinsam reparieren wir defekte Geräte."),
            ("Döörper Repair-Café", "Spenden für die Kaffeekasse sind willkommen."),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                self.assertEqual(infer_free_admission_price(title, description), "kostenlos")

    def test_seller_fee_without_visitor_statement_keeps_admission_unknown(self):
        self.assertEqual(
            infer_free_admission_price(
                "Familien Flohmarkt",
                "Der laufende Meter Standfläche kostet 10 €. Eine Anmeldung für Verkäufer ist nicht erforderlich.",
            ),
            "",
        )

    def test_explicit_free_visitor_admission_wins_beside_a_seller_fee(self):
        self.assertEqual(
            infer_free_admission_price(
                "Familien Flohmarkt",
                "Der Eintritt für Besucher ist frei. Der laufende Meter Standfläche kostet 10 €.",
            ),
            "kostenlos",
        )

    def test_does_not_infer_free_access_for_ticketed_or_ambiguous_markets(self):
        cases = [
            ("Nachtflohmarkt", "Tickets im Vorverkauf.", ""),
            ("Indoor-Flohmarkt", "In der Stadthalle.", ""),
            ("Flohmarkt Spezial", "Besuchereintritt 4 Euro.", ""),
            ("Flohmarkt Spezial", "", "4 Euro"),
            ("Designmarkt", "Lokale Labels und Kunsthandwerk.", ""),
            ("St. Pantaleon Kirmes", "Traditionelles Kirmesprogramm.", ""),
            ("Repair Café Sondertermin", "Besuchereintritt 4 Euro.", ""),
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
