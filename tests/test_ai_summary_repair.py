"""Semantic removal decisions replace writer retries without weakening validators."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from nrw_events import ai_decisions, ai_enrichment, ai_summary_repair
from nrw_events.decisions import DecisionError

from tests.test_ai_enrichment import FACTS, FakeClient, event

GOOD = "Bei Klangraum steht Kammermusik auf dem Programm. Das Ensemble spielt im Alten Rathaus in Bonn."
BAD = GOOD + " Der Eintritt ist frei."


def removal(choice="safe", probability=0.995):
    return {"model": "typesafe/jev-1.13", "answers": {"removal": {
        "type": "choice", "choice": choice, "confidence": probability,
        "probabilities": {choice: probability, "rewrite" if choice == "safe" else "safe": 1 - probability},
    }}, "usage": {"input_tokens": 123, "output_tokens": 5, "cost": 0.00001}}


class SummaryRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = ai_enrichment.AISettings(enabled=True, api_key="fake", model="fake",
            cache_db=Path(self.tmp.name) / "cache.sqlite3", jev_enabled=True, jev_api_key="fake")

    def test_removal_avoids_second_writer_and_accounts_for_decision(self):
        source = event(description="", description_html="", venue="Altes Rathaus",
                       category_key="concert", category_confidence=1)
        writer = FakeClient([{"ai_summary": BAD}])
        with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", return_value=removal()) as api:
            result = ai_enrichment.enrich_event(source, settings=self.settings, client=writer)
        self.assertEqual(result["ai_summary"], GOOD)
        self.assertEqual([call["stage"] for call in writer.calls], ["summary"])
        api.assert_called_once()
        with closing(sqlite3.connect(self.settings.cache_db)) as db:
            row = db.execute("SELECT stage2_attempts, input_tokens, output_tokens, cost_usd, stage2_json FROM ai_event_enrichment").fetchone()
        self.assertEqual(row[:3], (1, 223, 55))
        self.assertAlmostEqual(row[3], .00001)
        self.assertEqual(json.loads(row[4])["_jev_repair"]["error"], "summary invents free admission")

    def test_unsafe_uncertain_and_failed_decisions_keep_writer_retry(self):
        for response in (removal("rewrite"), removal(probability=.8), DecisionError("timeout")):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as directory:
                source = event(description="", description_html="", venue="Altes Rathaus", category_key="concert", category_confidence=1)
                settings = ai_enrichment.AISettings(**{**{name: getattr(self.settings, name) for name in self.settings.__dataclass_fields__}, "cache_db": Path(directory) / "db"})
                writer = FakeClient([{"ai_summary": BAD}, {"ai_summary": GOOD}])
                kwargs = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
                with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", **kwargs):
                    result = ai_enrichment.enrich_event(source, settings=settings, client=writer)
                self.assertEqual(result["ai_summary"], GOOD)
                self.assertEqual(len(writer.calls), 2)

    def test_locally_invalid_remainder_still_needs_full_retry(self):
        source = event(description="", description_html="", venue="Altes Rathaus", category_key="concert", category_confidence=1)
        writer = FakeClient([{"ai_summary": "Kammermusik in Bonn. Der Eintritt ist frei."}, {"ai_summary": GOOD}])
        with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", return_value=removal()):
            result = ai_enrichment.enrich_event(source, settings=self.settings, client=writer)
        self.assertEqual(result["ai_summary"], GOOD)
        self.assertEqual(len(writer.calls), 2)

    def test_copying_and_incomplete_sentences_are_never_deleted(self):
        for error in ("summary repeats a long source phrase", "summary ends mid-sentence"):
            with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate") as api:
                result = ai_summary_repair.repair(db, summary=BAD, error=error, facts=FACTS,
                                                model="test", api_key="fake", timeout_seconds=1)
                self.assertIsNone(result["summary"])
                api.assert_not_called()

    def test_repair_cache_bound_to_facts_and_exact_text(self):
        with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", return_value=removal()) as api:
            for _ in range(2):
                result = ai_summary_repair.repair(db, summary=BAD, error="summary invents free admission", facts=FACTS,
                                                model="test", api_key="fake", timeout_seconds=1)
            self.assertEqual(api.call_count, 1)
            self.assertEqual(result["usage"], {})
            ai_summary_repair.repair(db, summary=BAD, error="summary invents free admission", facts={**FACTS, "price": "20 Euro"},
                                    model="test", api_key="fake", timeout_seconds=1)
            self.assertEqual(api.call_count, 2)
