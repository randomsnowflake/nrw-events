import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common, report
from nrw_events.models import AdmissionDefault
from nrw_events.source_specs import AdapterType, SourceSpec, adapter_for
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window


class AdmissionDefaultTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 1), datetime(2026, 7, 31))

    def test_make_event_applies_auditable_free_by_nature_default(self):
        event = common.make_event(
            "Offene Reparaturwerkstatt",
            datetime(2026, 7, 4, 10),
            datetime(2026, 7, 4, 14),
            "Gemeindezentrum",
            "Bonn",
            "Gemeinsam reparieren wir defekte Geräte.",
            "https://example.test/repair",
            "Repair-Initiative",
            "repair nachhaltigkeit",
            admission=AdmissionDefault.FREE_BY_NATURE,
        )

        self.assertEqual(event["price"], "kostenlos")
        self.assertEqual(event["admission_basis"], "implicit")
        canonical = canonicalize_event(event)
        self.assertEqual(canonical.price, "kostenlos")
        self.assertEqual(canonical.admission_basis, "implicit")
        self.assertEqual(canonical.to_dict()["admission_basis"], "implicit")
        self.assertEqual(canonical.admission["isFree"], True)
        self.assertEqual(canonical.admission["basis"], "implicit")

    def test_explicit_visitor_price_prevents_free_by_nature_default(self):
        event = common.make_event(
            "Offene Reparaturwerkstatt",
            datetime(2026, 7, 4, 10),
            datetime(2026, 7, 4, 14),
            "Gemeindezentrum",
            "Bonn",
            "Eintritt für Besucher: 4 Euro.",
            "https://example.test/repair",
            "Repair-Initiative",
            "repair nachhaltigkeit",
            admission=AdmissionDefault.FREE_BY_NATURE,
        )

        self.assertEqual(event["price"], "")
        self.assertEqual(event["admission_basis"], "")

    def test_canonical_admission_distinguishes_paid_donation_and_inferred_free(self):
        cases = (
            (
                {"price": "12 Euro", "admission_basis": "explicit"},
                {"isFree": False, "amount": 12.0, "basis": "structured", "donationSuggested": False},
            ),
            (
                {"price": "Hutspende erbeten", "admission_basis": "explicit"},
                {"isFree": True, "amount": None, "basis": "structured", "donationSuggested": True},
            ),
            (
                {"description": "Der Eintritt ist frei."},
                {"isFree": True, "amount": None, "basis": "inferred", "donationSuggested": False},
            ),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                canonical = canonicalize_event(
                    {
                        "title": "Kulturabend",
                        "source": "Official",
                        "date": "2026-07-04",
                        "score": 2.0,
                        "city": "Bonn",
                        **overrides,
                    }
                )
                self.assertEqual(
                    {key: canonical.admission[key] for key in expected},
                    expected,
                )

    def test_euro_sign_visitor_price_prevents_free_by_nature_default(self):
        for description in (
            "Eintritt: 4,50 €",
            "Eintritt 12 €",
            "Ticketpreis 8 €",
        ):
            with self.subTest(description=description):
                event = common.make_event(
                    "Offene Reparaturwerkstatt",
                    datetime(2026, 7, 4, 10),
                    datetime(2026, 7, 4, 14),
                    "Gemeindezentrum",
                    "Bonn",
                    description,
                    "https://example.test/repair",
                    "Repair-Initiative",
                    "repair nachhaltigkeit",
                    admission=AdmissionDefault.FREE_BY_NATURE,
                )

                self.assertEqual(event["price"], "")
                self.assertEqual(event["admission_basis"], "")

    def test_source_spec_passes_admission_default_to_standard_adapter(self):
        spec = SourceSpec(
            "repair-feed",
            "Repair Feed",
            ("https://example.test/events.ics",),
            AdapterType.ICAL,
            "Bonn",
            admission=AdmissionDefault.FREE_BY_NATURE,
        )
        payload = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Offene Werkstatt
DTSTART:20260704T100000
DTEND:20260704T140000
DESCRIPTION:Gemeinsam reparieren wir defekte Geräte.
END:VEVENT
END:VCALENDAR"""

        with mock.patch.object(common, "fetch_url", return_value=payload):
            events = adapter_for(spec)()

        self.assertEqual(events[0]["price"], "kostenlos")
        self.assertEqual(events[0]["admission_basis"], "implicit")

    def test_source_spec_passes_opt_in_locked_category_to_standard_adapter(self):
        spec = SourceSpec(
            "single-purpose-cinema",
            "Single Purpose Cinema",
            ("https://example.test/events.ics",),
            AdapterType.ICAL,
            "Bonn",
            default_category_key="cinema",
            category_locked=True,
        )
        payload = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Rahmenprogramm: Führung und Filmvorführung
DTSTART:20260704T200000
DTEND:20260704T220000
END:VEVENT
END:VCALENDAR"""

        with mock.patch.object(common, "fetch_url", return_value=payload):
            events = adapter_for(spec)()

        self.assertEqual(events[0]["category_key"], "cinema")
        self.assertEqual(events[0]["category_reason"], "source:locked-default:cinema")

    def test_deduplication_carries_admission_basis_with_enriched_price(self):
        winner = canonicalize_event(
            {
                "title": "Offene Werkstatt",
                "source": "Official",
                "date": "2026-07-04",
                "score": 2.0,
                "city": "Bonn",
            }
        )
        duplicate = canonicalize_event(
            {
                "title": "Offene Werkstatt",
                "source": "Directory",
                "date": "2026-07-04",
                "score": 1.0,
                "city": "Bonn",
                "price": "kostenlos",
                "admission_basis": "implicit",
            }
        )

        merged = report.deduplicate([winner, duplicate])[0]

        self.assertEqual(merged.price, "kostenlos")
        self.assertEqual(merged.admission_basis, "implicit")
        self.assertEqual(merged.admission["isFree"], True)


if __name__ == "__main__":
    unittest.main()
