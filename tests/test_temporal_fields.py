import unittest

from nrw_events.validation import canonical_temporal_fields


class TemporalFieldsTests(unittest.TestCase):
    def test_exclusive_all_day_interval_crosses_dst_without_changing_source(self):
        raw = {"start_date": "2026-03-29", "end_date": "2026-03-29", "time": "",
               "all_day": True, "start_at": "2026-03-29T00:00:00+01:00",
               "end_at": "2026-03-30T00:00:00+02:00"}
        before = dict(raw)
        result = canonical_temporal_fields(raw)
        self.assertTrue(result["all_day"])
        self.assertEqual(result["end_date"], "2026-03-29")
        self.assertEqual(result["end_at"], raw["end_at"])
        self.assertEqual(raw, before)

    def test_overnight_clock_repairs_end_and_keeps_warning_input_unchanged(self):
        raw = {"start_date": "2026-09-22", "time": "23:00–01:00",
               "quality_warnings": []}
        result = canonical_temporal_fields(raw)
        self.assertTrue(result["end_at"].startswith("2026-09-23T01:00"))
        self.assertEqual(raw["quality_warnings"], [])
        self.assertNotIn("end_at", raw)
