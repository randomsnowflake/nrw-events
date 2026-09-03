"""Only known operational timestamps may be ignored in equivalence proof."""

import copy
import unittest

from nrw_events.snapshot_compare import differences


class SnapshotCompareTests(unittest.TestCase):
    def test_explicit_operational_paths_are_ignored(self):
        left = {
            "run_id": "one", "generated_at": "yesterday", "events_path": "/tmp/one/events.json",
            "timings": {"total_import_duration_ms": 10},
            "source_results": {"Source": {
                "duration_ms": 10, "ai_duration_ms": 2,
                "endpoints": {"https://example.test/a": {"duration_ms": 3, "bytes": 50}},
            }},
        }
        right = copy.deepcopy(left)
        right["run_id"] = "two"
        right["generated_at"] = "today"
        right["events_path"] = "/tmp/two/events.json"
        right["timings"]["total_import_duration_ms"] = 20
        right["source_results"]["Source"]["duration_ms"] = 99
        right["source_results"]["Source"]["endpoints"]["https://example.test/a"]["duration_ms"] = 9
        self.assertEqual(differences(left, right), [])

    def test_nested_series_run_id_is_not_operational_run_id(self):
        left = {"events": [{"event_id": "id", "run_id": "series-occurrence-one"}]}
        right = {"events": [{"event_id": "id", "run_id": "series-occurrence-two"}]}
        self.assertEqual(differences(left, right), ["/events/0/run_id: value changed"])

    def test_unknown_timing_field_is_not_silently_ignored(self):
        self.assertTrue(differences({"timings": {"policy": 1}}, {"timings": {"policy": 2}}))

    def test_health_counts_warnings_order_and_types_are_strict(self):
        for left, right in [
            ({"events": [1, 2]}, {"events": [2, 1]}),
            ({"retained_event_count": 1}, {"retained_event_count": 2}),
            ({"warnings": []}, {"warnings": ["new"]}),
            ({"status": "healthy"}, {"status": "degraded"}),
            ({"count": 1}, {"count": True}),
            ({"value": None}, {}),
        ]:
            with self.subTest(left=left, right=right):
                self.assertTrue(differences(left, right))

    def test_diagnostics_escape_json_pointers_and_never_emit_event_values(self):
        self.assertEqual(differences({"a/b~c": "private text"}, {"a/b~c": "secret"}), [
            "/a~1b~0c: value changed",
        ])

    def test_inputs_are_not_modified(self):
        left = {"run_id": "a", "events": [{"title": "title"}]}
        original = copy.deepcopy(left)
        differences(left, {"run_id": "b", "events": []})
        self.assertEqual(left, original)


if __name__ == "__main__":
    unittest.main()
