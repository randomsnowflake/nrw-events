"""Explicit suspensions must remain visible and must never fetch upstream."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from nrw_events.health import SourceStatus
from nrw_events.source_execution import _run_source
from nrw_events.source_specs import load_source_specs, typed_adapter_for
from nrw_events.sources import SOURCE_FETCHERS, SOURCE_SPECS


class FailedMarketSourceTests(unittest.TestCase):
    def test_suspended_sources_keep_identity_and_report_disabled_without_fetching(self):
        for source_id in ("rhein-antik", "bonn-district-festivals"):
            with self.subTest(source_id=source_id):
                spec = next(spec for spec in SOURCE_SPECS if spec.id == source_id)
                self.assertTrue(spec.disabled_reason)
                with patch("nrw_events.source_specs.adapter_for") as adapter:
                    fetch = typed_adapter_for(spec)
                    result, events = _run_source(spec.display_name, fetch)
                adapter.assert_not_called()
                self.assertEqual(result.source_id, source_id)
                self.assertEqual(result.status, SourceStatus.DISABLED)
                self.assertEqual(result.status_reason, spec.disabled_reason)
                self.assertEqual(events, [])
                self.assertEqual(SOURCE_FETCHERS[spec.display_name]().status, SourceStatus.DISABLED)

    def test_reactivation_uses_existing_adapter_and_preserves_errors(self):
        for source_id in ("rhein-antik", "bonn-district-festivals"):
            with self.subTest(source_id=source_id):
                spec = next(spec for spec in SOURCE_SPECS if spec.id == source_id)
                active = replace(spec, disabled_reason="")
                with patch("nrw_events.source_specs.adapter_for") as adapter:
                    adapter.return_value.side_effect = RuntimeError("upstream failure")
                    fetch = typed_adapter_for(active)
                    with self.assertRaisesRegex(RuntimeError, "upstream failure"):
                        fetch()
                adapter.assert_called_once_with(active)

    def test_registry_rejects_invalid_disabled_reasons(self):
        for reason in (False, None, 123, "   "):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "registry.json"
                path.write_text(json.dumps({"schema_version": 1, "sources": [{
                    "id": "example", "display_name": "Example", "region": "bonn-region",
                    "adapter": "python", "callable": "nrw_events.sources.rhein_antik:fetch",
                    "disabled_reason": reason,
                }]}))
                with self.assertRaisesRegex(ValueError, "disabled_reason"):
                    load_source_specs(path)
