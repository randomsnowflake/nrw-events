"""Routing regressions: no lost content, no new facts, bounded fallback and cache reuse."""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from nrw_events import ai_decisions, ai_enrichment, ai_policy
from nrw_events.decisions import DecisionError

from tests.test_ai_enrichment import FACTS, FakeClient, event
from tests.test_ai_enrichment import SUMMARY as RICH_SUMMARY

SUMMARY = {**RICH_SUMMARY, "ai_summary": "Das Konzert Klangraum findet im Alten Rathaus statt. Der Veranstaltungsort liegt in Bonn."}


def decision(coverage="complete", category="concert", probability=0.995):
    answers = {}
    for name, choice in (("coverage", coverage), ("category", category)):
        options = ai_decisions.questions()[name]["criteria"]
        probabilities = {key: ((1 - probability) / (len(options) - 1)) for key in options}
        probabilities[choice] = probability
        answers[name] = {"type": "choice", "choice": choice, "confidence": probability, "probabilities": probabilities}
    return {"model": "typesafe/jev-1.13", "answers": answers,
            "usage": {"input_tokens": 321, "output_tokens": 0, "cost": 0.000013482}}


class DecisionsRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = ai_enrichment.AISettings(enabled=True, api_key="fake", model="fake",
            cache_db=Path(self.tmp.name) / "cache.sqlite3", jev_enabled=True, jev_api_key="fake")
        self.simple = event(description="Klangraum: Konzert im Alten Rathaus.", description_html="", venue="Altes Rathaus")

    def run_event(self, response, client=None, **overrides):
        client = client or FakeClient([copy.deepcopy(SUMMARY)])
        with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", return_value=response) as evaluate:
            result = ai_enrichment.enrich_event(event(**{**self.simple, **overrides}), settings=self.settings, client=client)
        return result, client, evaluate

    def test_replaces_facts_and_category_then_cache_avoids_all_requests(self):
        result, writer, evaluate = self.run_event(decision())
        self.assertTrue(result["ai_summary"])
        self.assertEqual([call["stage"] for call in writer.calls], ["summary"])
        self.assertNotIn("category_key", writer.calls[0]["schema"]["properties"])
        self.assertNotIn("category_taxonomy", writer.calls[0]["payload"]["field_policy"])
        self.assertNotIn("_jev", writer.calls[0]["payload"]["facts"])
        second, writer, evaluate = self.run_event(decision(), client=FakeClient([]))
        evaluate.assert_not_called()
        self.assertEqual(result, second)
        with closing(sqlite3.connect(self.settings.cache_db)) as db:
            row = db.execute("SELECT input_tokens, stage1_json FROM ai_event_enrichment").fetchone()
            self.assertEqual(row[0], 421)  # Jev + one writer, counted exactly once
            self.assertTrue(json.loads(row[1])["_jev"]["facts_replaced"])

    def test_additional_programme_and_uncertainty_keep_extraction(self):
        for response in (decision(coverage="extract"), decision(probability=0.8)):
            with self.subTest(response=response):
                self.settings = replace(self.settings, cache_db=Path(self.tmp.name) / str(id(response)))
                _, writer, _ = self.run_event(response, FakeClient([copy.deepcopy(FACTS), copy.deepcopy(SUMMARY)]))
                self.assertEqual([call["stage"] for call in writer.calls], ["facts", "summary"])

    def test_structured_only_needs_no_semantic_completeness_guess(self):
        result, writer, _ = self.run_event(decision(probability=0.8), description="", description_html="")
        self.assertTrue(result["ai_summary"])
        self.assertEqual([call["stage"] for call in writer.calls], ["summary"])
        self.assertIn("category_key", writer.calls[0]["schema"]["properties"])

    def test_rehydrated_label_bound_material_skips_extraction(self):
        # Publication stores private source material in description, including
        # label-bound fallback material when the adapter supplied no prose.
        source = event(**{**self.simple, "description": "", "description_html": ""})
        material = ai_policy._source_material(source)
        result, writer, _ = self.run_event(decision(probability=0.8), description=material, description_html="")
        self.assertTrue(result["ai_summary"])
        self.assertEqual([call["stage"] for call in writer.calls], ["summary"])

    def test_provider_failure_keeps_extraction(self):
        writer = FakeClient([copy.deepcopy(FACTS), copy.deepcopy(SUMMARY)])
        with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=DecisionError("timeout")):
            result = ai_enrichment.enrich_event(self.simple, settings=self.settings, client=writer)
        self.assertTrue(result["ai_summary"])
        self.assertEqual([call["stage"] for call in writer.calls], ["facts", "summary"])

    def test_complex_prices_invalid_dates_and_long_text_skip_jev(self):
        for changes in ({"price": "Standgebühr 20 €"}, {"price": "10 €, Kinder frei"},
                        {"start_date": "invalid"}, {"description": "x" * 4001},
                        {"availability": "invalid"}):
            with self.subTest(changes=changes):
                payload = ai_policy._input_payload(event(**changes), changes.get("description", "Text"))
                with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate") as evaluate:
                    self.assertIsNone(ai_decisions.route(db, payload, model="test", api_key="fake", timeout_seconds=1))
                    evaluate.assert_not_called()

    def test_candidate_prices_are_exact_and_unknown_stays_unknown(self):
        for price, expected in (("", None), ("Eintritt frei", 0), ("12,50 €", 12.5)):
            payload = ai_policy._input_payload(event(price=price), "Text")
            facts = ai_decisions.candidate_facts(payload)
            self.assertEqual(facts["admission"]["amount"], expected)
            self.assertEqual(facts["program"], [])
            self.assertEqual(facts["title"], payload["title"])

    def test_fallback_decisions_cached_by_model_rubric_and_input(self):
        payload = ai_policy._input_payload(self.simple, "Text")
        with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", return_value=decision("extract")) as evaluate:
            for _ in range(2):
                result = ai_decisions.route(db, payload, model="test", api_key="fake", timeout_seconds=1)
                self.assertIsNone(result["facts"])
            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(result["usage"], {})
            ai_decisions.route(db, payload, model="changed", api_key="fake", timeout_seconds=1)
            self.assertEqual(evaluate.call_count, 2)

    def test_locked_category_preserved(self):
        result, _, _ = self.run_event(decision(), category_key="stage", category_confidence=1.0)
        self.assertEqual(result["category_key"], "stage")

    def test_disabled_uses_legacy_calls(self):
        self.settings = replace(self.settings, jev_enabled=False)
        _, writer, evaluate = self.run_event(decision(), FakeClient([copy.deepcopy(FACTS), copy.deepcopy(SUMMARY)]))
        evaluate.assert_not_called()
        self.assertEqual(len(writer.calls), 2)
