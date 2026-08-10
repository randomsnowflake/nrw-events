import unittest

from nrw_events.validation import EventValidationError, validate_event


def event(**overrides):
    candidate = {
        "title": "Testveranstaltung",
        "source": "Test",
        "source_id": "test",
        "start_date": "2026-08-14",
        "end_date": "2026-08-14",
        "city": "Bonn",
        "venue": "Marktplatz",
        "description": "Ein öffentlicher Termin.",
        "price": "",
        "link": "https://example.test/event",
        "score": 1.0,
        "distance_km": 1.0,
        "category_key": "festival",
        "category_label": "Feste & Stadtleben",
    }
    candidate.update(overrides)
    return validate_event(candidate)


class EventTypeTests(unittest.TestCase):
    def test_classifies_kirmes_and_rummel_titles_as_funfairs(self):
        for title in ("Kirmes Röttgen", "Herbstkirmes Duisdorf", "Frühlingsrummel"):
            with self.subTest(title=title):
                self.assertEqual(event(title=title).event_types, ["funfair"])

    def test_explicit_bonnkirmes_source_is_strong_evidence(self):
        self.assertEqual(
            event(title="Osterfest Beuel", source_id="bonnkirmes").event_types,
            ["funfair"],
        )

    def test_does_not_turn_a_market_at_a_kirmes_into_the_funfair(self):
        result = event(
            title="Floh-, Trödel- & Jahrmarkt Wahlscheider Kirmes",
            category_key="market",
            category_label="Märkte & Flohmärkte",
        )
        self.assertEqual(result.event_types, [])

    def test_description_or_broad_festival_category_is_not_enough(self):
        self.assertEqual(
            event(title="Buntes Treiben", description="Programm im Rahmen der Kirmes").event_types,
            [],
        )
        self.assertEqual(
            event(title="Schützen- und Volksfest").event_types,
            [],
        )

    def test_rejects_unknown_or_malformed_explicit_types(self):
        with self.assertRaisesRegex(EventValidationError, "event_types_item_invalid"):
            event(event_types=["trade-fair"])
        with self.assertRaisesRegex(EventValidationError, "event_types_type"):
            event(event_types="funfair")


if __name__ == "__main__":
    unittest.main()
