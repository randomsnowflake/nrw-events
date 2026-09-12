"""Visitor admission regression cases at the canonical publication boundary."""
import unittest

from nrw_events.validation import validate_event


class AdmissionContractTests(unittest.TestCase):
    def test_german_amounts_and_mixed_ticket_tiers(self) -> None:
        for price, amount in (
            ("1.200,00 EUR", 1200),
            ("Kinder 0 €, Erwachsene 12 €", 12),
            ("Erwachsene 12 €, Kinder 0 €", 12),
            ("10–20 €", 10),
        ):
            with self.subTest(price=price):
                event = validate_event({
                    "title": "Testkonzert", "source": "Test", "source_id": "test",
                    "start_date": "2026-10-24", "end_date": "2026-10-24",
                    "city": "Bonn", "venue": "Brotfabrik", "description": "Konzert.",
                    "link": "https://example.test/event", "score": 2.0,
                    "price": price, "admission_basis": "explicit",
                })
                self.assertEqual(event.admission["amount"], amount)
                self.assertIs(event.admission["isFree"], False)
                self.assertEqual(event.admission["note"], price)
