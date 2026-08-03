import json
import unittest
from datetime import datetime

from nrw_events import common
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window


class JsonLdScheduleTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 6, 8), datetime(2026, 6, 21))

    def test_ongoing_ranges_display_current_future_date_only(self):
        ev = common.make_event(
            "Long exhibition",
            datetime(2026, 4, 1),
            datetime(2026, 8, 1),
            "Museum",
            "Bonn",
            "Ongoing exhibition",
            "https://example.test/exhibition",
            "Museum",
            "ausstellung",
        )

        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev["date"], "2026-04-01")
        self.assertEqual(ev["end_date"], "2026-08-01")
        self.assertTrue(ev["ongoing"])

    def test_event_links_decode_html_entities(self):
        ev = common.make_event(
            "Energie-Dialog",
            datetime(2026, 6, 12),
            None,
            "",
            "Meckenheim",
            "",
            "https://www.meckenheim.de/detail.php?object=tx,3947.4.1&amp;ModID=11&amp;FID=3947.579.1",
            "Meckenheim",
            "lokal",
        )

        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(
            ev["link"],
            "https://www.meckenheim.de/detail.php?object=tx,3947.4.1&ModID=11&FID=3947.579.1",
        )

    def test_event_links_encode_internationalized_hostnames(self):
        ev = common.make_event(
            "MittwochsTreff",
            datetime(2026, 6, 17),
            None,
            "",
            "Wachtberg",
            "",
            "https://www.flüchtlingshilfe-wachtberg.de",
            "Wachtberg",
            "Begegnung",
        )

        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev["link"], "https://www.xn--flchtlingshilfe-wachtberg-gwc.de")

    def test_event_schedule_expands_future_appointments_and_skips_season_span(self):
        payload = {
            "@context": "https://schema.org",
            "@type": "Event",
            "name": "Rheinauen-Flohmarkt",
            "url": "https://www.bonn.de/flohmarkt-rheinaue.php",
            "description": "Flohmarkt in der Rheinaue",
            "location": {
                "@type": "Place",
                "name": "Rheinaue",
                "address": {"@type": "PostalAddress", "addressLocality": "Bonn"},
            },
            "startDate": "2026-04-18",
            "endDate": "2026-10-17",
            "eventSchedule": [
                {
                    "@type": "Schedule",
                    "startDate": "2026-04-18",
                    "endDate": "2026-04-18",
                    "startTime": "08:00",
                    "endTime": "18:00",
                },
                {
                    "@type": "Schedule",
                    "startDate": "2026-06-20",
                    "endDate": "2026-06-20",
                    "startTime": "08:00",
                    "endTime": "18:00",
                },
                {
                    "@type": "Schedule",
                    "startDate": "2026-07-18",
                    "endDate": "2026-07-18",
                    "startTime": "08:00",
                    "endTime": "18:00",
                },
            ],
        }
        html = f'<script type="application/ld+json">{json.dumps(payload)}</script>'

        events = common.events_from_jsonld(
            html, "Rheinauen-Flohmarkt", "Bonn", "markt flohmarkt outdoor", 1.0, payload["url"]
        )

        self.assertEqual(
            [ev["date"] for ev in events],
            ["2026-04-18", "2026-06-20", "2026-07-18"],
        )
        current = next(ev for ev in events if ev["date"] == "2026-06-20")
        self.assertEqual(current["time"], "08:00–18:00")
        self.assertNotIn("2026-04-18–2026-10-17", [ev["date"] for ev in events])

    def test_jsonld_structured_admission_overrides_text_inference(self):
        fixtures = [
            (
                "explicitly free",
                {"isAccessibleForFree": True},
                "Tickets kosten 12 EUR.",
                "kostenlos",
            ),
            (
                "explicitly not free",
                {"isAccessibleForFree": False},
                "Der Eintritt ist frei.",
                "kostenpflichtig",
            ),
            (
                "priced offer",
                {"offers": {"@type": "Offer", "price": "12.50", "priceCurrency": "EUR"}},
                "Der Eintritt ist frei.",
                "12.50 EUR",
            ),
            (
                "priced offer list",
                {
                    "offers": [
                        {"@type": "Offer", "availability": "https://schema.org/SoldOut"},
                        {"@type": "Offer", "price": 8, "priceCurrency": "EUR"},
                    ]
                },
                "",
                "8 EUR",
            ),
            (
                "zero-priced offer",
                {"offers": {"@type": "Offer", "price": 0, "priceCurrency": "EUR"}},
                "",
                "kostenlos",
            ),
            (
                "free tier does not make a mixed offer list free",
                {
                    "offers": [
                        {"@type": "Offer", "price": 0, "priceCurrency": "EUR"},
                        {"@type": "Offer", "price": 12, "priceCurrency": "EUR"},
                    ]
                },
                "",
                "12 EUR",
            ),
            (
                "bare zero without currency is not a free claim",
                {"offers": {"@type": "Offer", "price": 0}},
                "",
                "",
            ),
            (
                "bare zero without currency defers to a later priced offer",
                {
                    "offers": [
                        {"@type": "Offer", "price": 0},
                        {"@type": "Offer", "price": 15, "priceCurrency": "EUR"},
                    ]
                },
                "",
                "15 EUR",
            ),
            (
                "free flag wins over contradictory paid offer",
                {
                    "isAccessibleForFree": "true",
                    "offers": {"@type": "Offer", "price": 20, "priceCurrency": "EUR"},
                },
                "",
                "kostenlos",
            ),
        ]

        for label, admission, description, expected_price in fixtures:
            expected_basis = "explicit" if expected_price else ""
            with self.subTest(label=label):
                payload = {
                    "@context": "https://schema.org",
                    "@type": "Event",
                    "name": "Admission test event",
                    "startDate": "2026-06-12T19:00:00+02:00",
                    "description": description,
                    **admission,
                }
                html = f'<script type="application/ld+json">{json.dumps(payload)}</script>'

                events = common.events_from_jsonld(
                    html, "Test source", "Bonn", "konzert", 1.0, "https://example.test/event"
                )

                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["price"], expected_price)
                self.assertEqual(events[0]["admission_basis"], expected_basis)
                canonical = canonicalize_event(events[0])
                self.assertEqual(canonical.price, expected_price)
                self.assertEqual(canonical.admission_basis, expected_basis)


if __name__ == "__main__":
    unittest.main()
