"""Visitor admission regression cases at the canonical publication boundary."""
import json
import unittest
from pathlib import Path

from nrw_events.validation import validate_event


class AdmissionContractTests(unittest.TestCase):
    def test_german_amounts_and_mixed_ticket_tiers(self) -> None:
        for price, amount in (
            ("1.200,00 EUR", 1200),
            ("Kinder 0 €, Erwachsene 12 €", 12),
            ("Erwachsene 12 €, Kinder 0 €", 12),
            ("10–20 €", 10),
            ("Der Eintritt in den Park ist frei. Kosten für die Führungen: Erwachsene: 8 Euro, ermäßigt 2 Euro; Kinder bis 10 Jahre: kostenlos", 8),
            ("8 € / 2 € ermäßigt", 8),
            ("8 € (erm. 5 €)", 8),
            ("Ermäßigt 5 €, regulär 9 €", 9),
            ("Kinder frei, Erwachsene 10 €, ermäßigt 5 €", 10),
            ("Eintritt 10 €. Ermäßigt 6 €", 10),
            ("ermäßigt 4 €", 4),
            ("VVK 15 €, AK 18 €", 15),
            ("10 € / 5 € (Kinder/ermäßigt)", 10),
            ("10,00€ Normalpreis (8,00€ ermäßigt)", 10),
            ("12 Euro regulär, 9 Euro ermäßigt; 7–18 Jahre 6 Euro, bis 6 Jahre frei; donnerstags ab 16 Uhr 5 Euro", 12),
            ("Online regulär 18 Euro, Studierende 12 Euro; begrenzte Early-Bird-Tickets 10 Euro", 18),
            ("15 Euro regulär; 14 Euro mit Gästekarte Bad Neuenahr-Ahrweiler", 15),
            ("Vorverkauf 8 Euro, Tageskasse regulär 10 Euro", 8),
            ("13,90 Euro; Late Ticket mit Einlass ab 18:30 Uhr: 9,90 Euro", 13.9),
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

    def test_kulturrucksack_programme_rule_across_sources(self) -> None:
        for source in ("bonn-de-events", "sitekit-bruehl", "troisdorf"):
            for title, price, description, expected in (
                ("Kulturrucksack 2026: Club der Heldinnen", "", "Kreativer Workshop.", True),
                ("Digitaler Kulturrucksack: Stop-Motion", "", "Kreativer Workshop.", True),
                ("Kulturrucksack-NRW: Tanz", "", "Kreativer Workshop.", True),
                ("Kulturrucksack: Workshop", "12 Euro", "Kreativer Workshop.", False),
                ("Kulturrucksack: Workshop", "", "Teilnahmegebühr 12 Euro.", None),
                ("Kulturrucksack: Workshop", "", "Die Teilnahme ist kostenpflichtig.", False),
                ("Kulturworkshop", "", "Nebenan findet der Kulturrucksack statt.", None),
                ("Kulturrucksackverkauf", "", "Verkauf von Taschen.", None),
            ):
                with self.subTest(source=source, title=title, price=price, description=description):
                    event = validate_event({
                        "title": title, "source": source, "source_id": source,
                        "start_date": "2026-10-10", "end_date": "2026-10-10",
                        "city": "Bonn", "venue": "Brotfabrik", "description": description,
                        "link": "https://example.test/event", "score": 2.0, "price": price,
                    })
                    self.assertIs(event.admission["isFree"], expected)
                    if expected:
                        self.assertEqual(event.admission["basis"], "structured")

    def test_shared_price_vectors(self) -> None:
        vectors = json.loads((Path(__file__).parent / "data/admission-vectors.json").read_text())
        for vector in vectors:
            with self.subTest(case=vector["name"]):
                event = validate_event({
                    "title": "Testkonzert", "source": "Test", "source_id": "test",
                    "start_date": "2026-10-24", "end_date": "2026-10-24",
                    "city": "Bonn", "venue": "Brotfabrik", "description": vector["description"],
                    "link": "https://example.test/event", "score": 2.0,
                    "price": vector["price"],
                })
                self.assertEqual(event.admission["amount"], vector["producer"]["amount"])
                self.assertEqual(event.admission["isFree"], vector["producer"]["isFree"])
