import unittest

from nrw_events import report
from nrw_events.validation import canonicalize_event


def market(**overrides):
    event = {
        "title": "Flohmarkt",
        "source": "Official",
        "source_id": "official",
        "date": "2026-08-23",
        "start_date": "2026-08-23",
        "end_date": "2026-08-23",
        "time": "11:00–17:00",
        "city": "Bonn",
        "venue": "Marktplatz",
        "description": "Ein Flohmarkt für die ganze Familie.",
        "link": "https://example.test/flohmarkt",
        "score": 1.0,
    }
    event.update(overrides)
    return event


class ExhibitorInformationTests(unittest.TestCase):
    def test_rigalsche_wiese_keeps_seller_facts_separate_from_unknown_admission(self):
        event = canonicalize_event(market(
            title="Familien Flohmarkt auf der Rigal'schen Wiese",
            venue="Rigal'sche Wiese",
            description=(
                "Der Markt ist für Besucher von 11:00 bis 17:00 Uhr geöffnet.\n\n"
                "Eine Anmeldung ist nicht erforderlich. Der laufende Meter "
                "Standfläche kostet 10 €. Der Aufbau beginnt ab 7:00 Uhr."
            ),
        ))

        self.assertIsNone(event.admission["isFree"])
        self.assertEqual(event.price, "")
        self.assertEqual(event.time, "11:00–17:00")
        self.assertEqual(event.exhibitor["fee"]["amount"], 10.0)
        self.assertEqual(event.exhibitor["fee"]["unit"], "running_metre")
        self.assertEqual(event.exhibitor["setupTime"], "07:00")
        self.assertFalse(event.exhibitor["registration"]["required"])
        self.assertIn("für Besucher", event.description)
        self.assertNotIn("Standfläche", event.description)
        self.assertNotIn("Aufbau", event.description)

    def test_paid_visitors_and_free_sellers_remain_independent(self):
        event = canonicalize_event(market(
            description="Besuchereintritt 5 Euro.",
            price="5 €",
            admission_basis="explicit",
            exhibitor={
                "fee": {
                    "isFree": True,
                    "amount": 0,
                    "currency": "EUR",
                    "unit": "flat",
                    "basis": "structured",
                    "note": "Standplatz kostenlos",
                },
            },
        ))

        self.assertFalse(event.admission["isFree"])
        self.assertEqual(event.admission["amount"], 5.0)
        self.assertTrue(event.exhibitor["fee"]["isFree"])
        self.assertEqual(event.exhibitor["fee"]["amount"], 0.0)

    def test_deduplication_preserves_richer_exhibitor_facts(self):
        empty = canonicalize_event(market()).to_dict()
        enriched = canonicalize_event(market(
            source="Second official source",
            exhibitor={
                "fee": {
                    "isFree": False,
                    "amount": 10,
                    "currency": "EUR",
                    "unit": "running_metre",
                    "basis": "structured",
                    "note": "10 € pro laufendem Meter",
                },
                "setupTime": "07:00",
            },
        )).to_dict()

        merged = report._merged_exhibitor_information(empty, enriched)

        self.assertEqual(merged["fee"]["amount"], 10.0)
        self.assertEqual(merged["setupTime"], "07:00")


if __name__ == "__main__":
    unittest.main()
