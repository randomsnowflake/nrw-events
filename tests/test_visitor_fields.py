import unittest

from nrw_events.validation import canonical_visitor_fields


class VisitorFieldsTests(unittest.TestCase):
    def test_editorial_paid_verdict_survives_conflicting_free_prose(self):
        admission = {"isFree": False, "amount": 8, "currency": "EUR", "basis": "editorial",
                     "note": "8 Euro", "donationSuggested": False}
        raw = {"title": "Test", "source": "Bonn.de Events", "source_id": "bonn",
               "description": "Eintritt frei", "price": "8 Euro", "admission_basis": "editorial",
               "admission": admission}
        result = canonical_visitor_fields(raw, "scraped")
        self.assertEqual(result["admission"], admission)
        result["admission"]["note"] = "changed"
        self.assertEqual(admission["note"], "8 Euro")

    def test_inferred_free_warning_does_not_mutate_input_warning_list(self):
        raw = {"title": "Test", "source": "Bonn.de Events", "source_id": "bonn",
               "description": "", "price": "frei", "admission_basis": "implicit",
               "quality_warnings": []}
        result = canonical_visitor_fields(raw, "scraped")
        self.assertIsNone(result["admission"]["isFree"])
        self.assertEqual(raw["quality_warnings"], [])
