import ast
import importlib
import json
import os
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nrw_events import common, dates, location, scoring
from nrw_events.health import SourceStatus
from nrw_events.models import CanonicalEvent, RawEvent
from nrw_events.source_specs import load_source_specs
from nrw_events.source_types import SourceFetcher, TextParser
from nrw_events.validation import canonicalize_event
from nrw_events.sources import SOURCES, SOURCE_FETCHERS, SOURCE_IDS, SOURCE_SPECS, harmonie


class ModuleBoundaryTests(unittest.TestCase):
    def test_quality_import_does_not_load_core(self):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        environment = {**os.environ, "PYTHONPATH": str(scripts)}
        completed = subprocess.run(
            [sys.executable, "-c", "import sys; import nrw_events.quality; assert 'nrw_events.core' not in sys.modules"],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_common_compatibility_import_has_one_canonical_module_identity(self):
        self.assertIs(importlib.import_module("nrw_events.common"), common)
        self.assertEqual(common.__name__, "nrw_events.core")
        self.assertNotIn("scripts.nrw_events", sys.modules)

    def test_common_facade_reexports_stable_location_and_scoring_helpers(self):
        self.assertIs(common.haversine, location.haversine)
        self.assertIs(common.category_score, scoring.category_score)
        self.assertIs(common.parse_date, dates.parse_date)

    def test_event_record_and_callable_contracts_are_importable(self):
        event: RawEvent = {"title": "Event", "source": "Test", "score": 1.0}
        self.assertEqual(event["title"], "Event")
        self.assertTrue(SourceFetcher)
        self.assertTrue(TextParser)

    def test_canonical_event_is_immutable_after_validation(self):
        event_date = common.TODAY.strftime("%Y-%m-%d")
        event = canonicalize_event({
            "title": "Event", "source": "Test", "date": event_date,
            "score": 1.0, "city": "Bonn",
        })
        self.assertIsInstance(event, CanonicalEvent)
        self.assertEqual(event["start_date"], event_date)
        with self.assertRaises(AttributeError):
            event.title = "Changed"

    def test_source_registry_has_unique_stable_ids(self):
        ids = [spec.id for spec in SOURCE_SPECS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(SOURCES), set(SOURCE_IDS))
        self.assertEqual(set(SOURCE_FETCHERS), set(SOURCES))

    def test_source_registry_rejects_empty_component_ids(self):
        payload = {
            "schema_version": 1,
            "sources": [{
                "id": "test-source",
                "display_name": "Test source",
                "region": "test",
                "adapter": "python",
                "callable": "nrw_events.sources.harmonie:fetch",
                "component_ids": [""],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid component id"):
                load_source_specs(path)

    def test_html_fetch_calls_have_registered_machine_readable_source_ids(self):
        registered = {
            identity
            for spec in SOURCE_SPECS
            for identity in (spec.id, *spec.component_ids)
        }
        source_dir = Path(__file__).resolve().parents[1] / "scripts/nrw_events/sources"
        for path in source_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in ast.walk(tree):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "fetch_html_events"
                ):
                    continue
                keywords = {keyword.arg: keyword.value for keyword in call.keywords}
                self.assertIn("source_id", keywords, f"{path.name}:{call.lineno}")
                value = keywords["source_id"]
                if isinstance(value, ast.Constant):
                    self.assertIn(value.value, registered, f"{path.name}:{call.lineno}")

    def test_removed_sources_are_not_registered(self):
        self.assertNotIn("Songkick", SOURCES)
        self.assertNotIn("Rausgegangen Party", SOURCES)

    def test_harmonie_exposes_its_reachable_typed_success_result(self):
        with patch.object(harmonie.common, "fetch_ical", return_value=[{"title": "Concert"}]):
            result = harmonie.fetch()
        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.events, ({"title": "Concert"},))
