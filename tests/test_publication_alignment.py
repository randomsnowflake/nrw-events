"""Fault injection at optional publication-enrichment boundaries."""
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import ai_enrichment, common, runner, series
from nrw_events.identity import event_id

from tests.helpers import make_event, make_runner_env


class PublicationAlignmentTests(unittest.TestCase):
    def test_bad_optional_batches_preserve_every_original_occurrence(self) -> None:
        raw = [make_event(title=title, source="Test Source", source_id="test-source", time=time)
               for title, time in (("Alpha concert", "18:00"), ("Beta concert", "20:00"))]
        def mutate(rows, mode):
            if mode == "short":
                return rows[:-1]
            if mode == "long":
                return [*rows, rows[0]]
            if mode == "reordered":
                return list(reversed(rows))
            return [{**rows[0], "title": "Unrelated occurrence"}, *rows[1:]]

        with make_runner_env() as env:
            context = env.context(series_ledger_json="", clock=lambda: datetime(2026, 6, 8, 12))
            with patch.object(ai_enrichment, "is_target_event", return_value=False), patch.object(common, "flush_detail_page_caches", return_value=[]):
                baseline = runner.run_import(context, {"Test Source": lambda: raw})
            self.assertEqual(2, len(baseline.events))
            for stage in ("ai", "series"):
                for mode in ("short", "long", "reordered", "wrong-owner"):
                    with self.subTest(stage=stage, mode=mode):
                        def ai_output(rows, mode=mode, stage=stage, **kwargs):
                            return mutate(rows, mode) if stage == "ai" else rows
                        def series_output(rows, ledger, mode=mode, stage=stage, **kwargs):
                            values = list(rows)
                            return (mutate(values, mode) if stage == "series" else values, [], ledger)
                        with patch.object(ai_enrichment, "is_target_event", return_value=True), \
                             patch.object(ai_enrichment, "enrich_events", side_effect=ai_output), \
                             patch.object(series, "enrich_events", side_effect=series_output), \
                             patch.object(common, "flush_detail_page_caches", return_value=[]):
                            result = runner.run_import(context, {"Test Source": lambda: raw})
                        self.assertEqual([event_id(e) for e in baseline.events], [event_id(e) for e in result.events])
                        self.assertEqual([e.title for e in baseline.events], [e.title for e in result.events])
                        self.assertEqual("degraded", result.run_status)
                        self.assertTrue(result.warnings)
