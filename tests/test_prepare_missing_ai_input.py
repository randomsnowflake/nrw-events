import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "prepare_missing_ai_input.py"
SPEC = importlib.util.spec_from_file_location("prepare_missing_ai_input", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PrepareMissingAiInputTests(unittest.TestCase):
    def test_selects_only_restricted_events_that_are_still_completely_blank(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            current = root / "current.json"
            output = root / "output.json"
            events = [
                {"event_id": "blank", "source_id": "marktcom", "title": "Blank", "date": "2026-08-10", "description": "Source material"},
                {"event_id": "done", "source_id": "marktcom", "title": "Done", "date": "2026-08-10", "description": "Source material"},
                {"event_id": "normal", "source_id": "other", "title": "Normal", "date": "2026-08-10"},
            ]
            source.write_text(json.dumps({"events": events}), encoding="utf-8")
            current.write_text(json.dumps({"events": [
                {**events[0], "description": ""},
                {**events[1], "description": "", "ai_summary": "Generated"},
                events[2],
            ]}), encoding="utf-8")
            result = MODULE.prepare(source, current, output, enrich_details=False)
            prepared = json.loads(output.read_text(encoding="utf-8"))["events"]
            self.assertEqual([event["event_id"] for event in prepared], ["blank"])
            self.assertEqual(result["selected"], 1)
            self.assertEqual(result["with_source_material_before_detail"], 1)

    def test_fails_when_current_event_is_missing_from_source_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            current = root / "current.json"
            output = root / "output.json"
            source.write_text('{"events": []}', encoding="utf-8")
            current.write_text(json.dumps({"events": [
                {"event_id": "missing", "source_id": "radio-bonn-rhein-sieg", "title": "Missing", "date": "2026-08-10"}
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "1 current missing events"):
                MODULE.prepare(source, current, output, enrich_details=False)


if __name__ == "__main__":
    unittest.main()
