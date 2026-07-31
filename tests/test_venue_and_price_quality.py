import unittest

from nrw_events import common
from nrw_events.validation import canonicalize_event


normalize_venue_name = common.normalize_venue_name
admission_price_from_description = common.admission_price_from_description


def _event(**overrides):
    event = {
        "title": "Testtermin",
        "source": "Test",
        "start_date": "2026-08-05",
        "end_date": "2026-08-05",
        "city": "Bonn",
        "description": "",
        "price": "",
        "link": "https://example.test/event",
        "score": 1.0,
        "distance_km": 0,
    }
    event.update(overrides)
    return event


class VenueNormalizationTests(unittest.TestCase):
    def test_drops_postcode_town_country_and_room_detail(self):
        cases = [
            (
                "kleines theater, Koblenzer Str. 78, Bonn, 53177, Deutschland",
                "Kleines Theater, Koblenzer Str. 78",
            ),
            (
                "Schloss Eulenbroich Bildungswerkstatt, Raumnummer: Bildungswerkstatt",
                "Schloss Eulenbroich Bildungswerkstatt",
            ),
            (
                "Bürgersaal im Bergischen Hof, Rathausplatz, 51503 Rösrath",
                "Bürgersaal im Bergischen Hof, Rathausplatz",
            ),
            (
                "Museum für Rheinbreitbacher Alltagsgeschichte (Heimatmuseum), Hauptstraße 29, Rheinbreitbach",
                "Museum für Rheinbreitbacher Alltagsgeschichte (Heimatmuseum), Hauptstraße 29",
            ),
        ]

        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_venue_name(raw), expected)

    def test_keeps_the_street_that_tells_two_branches_apart(self):
        for raw in ("HIT-Markt, Alte Heerstraße 53", "HIT-Markt, Drachenburgstraße 14", "REWE, Ziegelweg 1"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_venue_name(raw), raw)

    def test_empties_a_venue_that_only_repeats_a_known_town(self):
        for raw in ("Brühl", "Hürth", "Wesseling", "Bonn", "Bonn-Beuel", "Bad Neuenahr-Ahrweiler"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_venue_name(raw), "")

    def test_keeps_venue_names_that_merely_contain_a_town(self):
        for raw in ("Kunstmuseum Bonn", "Marktplatz Linz", "Insel Grafenwerth-1 Bad Honnef"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_venue_name(raw), raw)


class DescriptionPriceExtractionTests(unittest.TestCase):
    def test_reads_an_admission_amount_stated_in_the_copy(self):
        cases = [
            ("Der Eintritt kostet 3 Euro.", "3 €"),
            ("Teilnahmegebühr: 5 Eur", "5 €"),
            ("Preis: 6,00 €", "6,00 €"),
            ("Tickets bekommt ihr ab 22 Eur.", "ab 22 €"),
        ]

        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(admission_price_from_description(description), expected)

    def test_ignores_amounts_that_are_not_admission(self):
        for description in (
            "",
            "Für 2 Euro gibt es ein Kölsch an der Theke.",
            "Wir treffen uns um 18 Uhr am Eingang.",
        ):
            with self.subTest(description=description):
                self.assertEqual(admission_price_from_description(description), "")

    def test_fills_an_empty_price_field_from_the_description(self):
        event = canonicalize_event(_event(description="Der Eintritt kostet 3 Euro."))

        self.assertEqual(event.price, "3 €")
        self.assertEqual(event.admission["amount"], 3.0)
        self.assertIs(event.admission["isFree"], False)
        self.assertEqual(event.admission["basis"], "inferred")

    def test_never_overrides_a_price_the_source_published(self):
        event = canonicalize_event(
            _event(price="ab 32 €", description="Der Eintritt kostet 3 Euro."),
        )

        self.assertEqual(event.price, "ab 32 €")

    def test_leaves_free_admission_alone(self):
        event = canonicalize_event(
            _event(description="Der Eintritt ist frei. Getränke kosten 3 Euro."),
        )

        self.assertEqual(event.price, "kostenlos")
        self.assertIs(event.admission["isFree"], True)


if __name__ == "__main__":
    unittest.main()
