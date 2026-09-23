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

from nrw_events import ai_decisions, ai_enrichment, ai_policy, publication_enrichment
from nrw_events.decisions import DecisionError
from nrw_events.validation import validate_event

from tests.helpers import make_event
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


def respond(response):
    def evaluate(*, questions, **kwargs):
        return {**response, "answers": {key: response["answers"][key] for key in questions}}
    return evaluate


class DecisionsRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = ai_enrichment.AISettings(enabled=True, api_key="fake", model="fake",
            cache_db=Path(self.tmp.name) / "cache.sqlite3", jev_enabled=True, jev_api_key="fake")
        self.simple = event(description="Klangraum: Konzert im Alten Rathaus.", description_html="", venue="Altes Rathaus")

    def run_event(self, response, client=None, **overrides):
        client = client or FakeClient([copy.deepcopy(SUMMARY)])
        with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=respond(response)) as evaluate:
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
            self.assertEqual(row[0], 421)  # One batched decision request + one writer, counted once
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
        self.assertEqual(set(writer.calls[0]["schema"]["properties"]), {"ai_summary"})

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

    def test_complex_prices_invalid_dates_and_long_text_keep_extraction(self):
        for changes in ({"price": "Standgebühr 20 €"}, {"price": "10 €, Kinder frei"},
                        {"start_date": "invalid"}, {"description": "x" * 4001},
                        {"availability": "invalid"}):
            with self.subTest(changes=changes):
                payload = ai_policy._input_payload(event(**changes), changes.get("description", "Text"))
                with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate") as evaluate:
                    result = ai_decisions.route(db, payload, model="test", api_key="fake", timeout_seconds=1, locked_category="concert")
                    self.assertIsNone(result["facts"])
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
        with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=respond(decision("extract"))) as evaluate:
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

    def test_structured_locked_event_needs_only_writer_and_no_jev(self):
        result, writer, evaluate = self.run_event(decision(), description="", description_html="",
                                                 category_key="concert", category_confidence=1)
        evaluate.assert_not_called()
        self.assertEqual([call["stage"] for call in writer.calls], ["summary"])
        self.assertEqual(set(writer.calls[0]["schema"]["properties"]), {"ai_summary"})
        self.assertEqual(result["category_key"], "concert")

    def test_category_cache_reused_across_occurrences_but_not_changed_programme(self):
        first = ai_policy._input_payload(self.simple, "Jazz und Kammermusik mit dem Ensemble.")
        second = {**first, "start_date": "2026-08-10", "end_date": "2026-08-10", "time": "17:00"}
        with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=respond(decision())) as evaluate:
            for item in (first, second):
                result = ai_decisions.route(db, item, model="test", api_key="fake", timeout_seconds=1, facts_known=True)
                self.assertEqual(result["metadata"]["category"], "concert")
            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(result["usage"], {})
            ai_decisions.route(db, {**second, "source_material": "Lesung statt Konzert."}, model="test", api_key="fake", timeout_seconds=1, facts_known=True)
            self.assertEqual(evaluate.call_count, 2)

    def test_locked_category_asks_only_about_missing_facts(self):
        _, writer, evaluate = self.run_event(decision(), category_key="stage", category_confidence=1)
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(set(evaluate.call_args.kwargs["questions"]), {"coverage"})
        self.assertNotIn("category_key", writer.calls[0]["schema"]["properties"])

    def test_metadata_comes_from_facts_not_writer(self):
        bogus = {**SUMMARY, "venue": "Berlin", "price": "100 Euro", "organizer": "Invented"}
        result, writer, _ = self.run_event(decision(), FakeClient([bogus]))
        self.assertEqual(result["venue"], "Altes Rathaus")
        self.assertNotEqual(result.get("price"), "100 Euro")
        self.assertNotEqual(result.get("organizer"), "Invented")
        self.assertEqual(set(writer.calls[0]["schema"]["properties"]), {"ai_summary"})

    def test_rich_prose_skips_predictably_negative_coverage_request(self):
        prose = "Ein Streichquartett spielt Beethoven. Anschließend erläutert die Komponistin ihre neue Uraufführung und beantwortet Publikumsfragen."
        writer = FakeClient([copy.deepcopy(FACTS), copy.deepcopy(SUMMARY)])
        result, writer, evaluate = self.run_event(decision(), writer, description=prose,
                                                category_key="concert", category_confidence=1)
        evaluate.assert_not_called()
        self.assertTrue(result["ai_summary"])
        self.assertEqual([c["stage"] for c in writer.calls], ["facts", "summary"])

    def test_structured_category_failure_never_causes_redundant_extraction(self):
        writer = FakeClient([copy.deepcopy(SUMMARY)])
        with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=DecisionError("timeout")):
            result = ai_enrichment.enrich_event(event(**{**self.simple, "description": "", "description_html": ""}),
                                               settings=self.settings, client=writer)
        self.assertTrue(result["ai_summary"])
        self.assertEqual([c["stage"] for c in writer.calls], ["summary"])
        self.assertEqual(result["category_key"], "other")

    def test_unresolved_questions_are_batched(self):
        _, _, api = self.run_event(decision())
        api.assert_called_once()
        request = api.call_args.kwargs
        self.assertEqual(set(request["questions"]), {"coverage", "category"})
        self.assertNotIn("start_date", request["state"])
        self.assertIn("candidate_facts", request["questions"]["coverage"]["instructions"])
        self.assertIsInstance(request["questions"]["category"]["instructions"], str)

    def test_occurrence_change_reuses_only_category_from_batched_cache(self):
        first = ai_policy._input_payload(self.simple, "Klangraum: Konzert im Alten Rathaus.")
        second = {**first, "start_date": "2026-08-10", "end_date": "2026-08-10"}
        with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=respond(decision())) as api:
            for item in (first, second):
                result = ai_decisions.route(db, item, model="test", api_key="fake", timeout_seconds=1)
                self.assertIsNotNone(result["facts"])
            self.assertEqual(api.call_count, 2)
            self.assertEqual(set(api.call_args.kwargs["questions"]), {"coverage"})
            self.assertEqual(result["facts"]["start_date"], second["start_date"])
            self.assertEqual(result["usage"]["input_tokens"], 321)

    def test_category_options_match_taxonomy_and_probability_policy(self):
        self.assertEqual(set(ai_decisions.questions()["category"]["criteria"]),
                         set(ai_decisions.category_taxonomy.CATEGORY_BY_KEY) | {"unknown"})
        for probability, expected in ((.979, None), (.98, "concert")):
            self.assertEqual(ai_decisions._accepted(decision(probability=probability)["answers"]["category"]), expected)

    def test_cached_category_survives_failed_coverage_batch(self):
        payload = ai_policy._input_payload(self.simple, "Klangraum: Konzert im Alten Rathaus.")
        with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=respond(decision())):
            ai_decisions.route(db, payload, model="test", api_key="fake", timeout_seconds=1, facts_known=True)
            with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=DecisionError("timeout")):
                result = ai_decisions.route(db, payload, model="test", api_key="fake", timeout_seconds=1)
            self.assertEqual(result["metadata"]["category"], "concert")
            self.assertIsNone(result["facts"])
            self.assertEqual(result["usage"], {})

    def test_partial_response_does_not_authorize_or_cache_a_partial_batch(self):
        payload = ai_policy._input_payload(self.simple, "Klangraum: Konzert im Alten Rathaus.")
        response = decision()
        del response["answers"]["coverage"]
        with closing(sqlite3.connect(":memory:")) as db, patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", return_value=response):
            result = ai_decisions.route(db, payload, model="test", api_key="fake", timeout_seconds=1)
            self.assertIsNone(result["facts"])
            self.assertIsNone(result["metadata"]["category"])
            self.assertEqual(db.execute("SELECT count(*) FROM ai_jev_decisions").fetchone()[0], 0)


class AdmissionDecisionTests(unittest.TestCase):
    def ask(self, connection, material, choice="free", probability=0.99):
        options = ai_decisions.ADMISSION_QUESTION["criteria"]
        probabilities = {key: (1 - probability) / (len(options) - 1) for key in options}
        probabilities[choice] = probability
        response = {"model": "typesafe/jev-1.13", "answers": {"admission": {
            "type": "choice", "choice": choice, "confidence": probability, "probabilities": probabilities}}}
        with patch.object(ai_decisions.OpenRouterDecisionClient, "evaluate", side_effect=respond(response)) as evaluate:
            price = ai_decisions.resolve_admission(connection, {"title": "Lesung", "venue": "Bücherei"}, material,
                                                   model="m", api_key="k", timeout_seconds=5)
        return price, evaluate.call_count

    def test_confident_answers_map_to_price_and_are_cached(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            self.assertEqual(self.ask(connection, "Der Eintritt ist frei."), ("free", 1))
            self.assertEqual(self.ask(connection, "Der Eintritt ist frei.", "paid"), ("free", 0))
            self.assertEqual(self.ask(connection, "Karten 12 Euro.", "paid"), ("paid", 1))
            self.assertEqual(self.ask(connection, "Spende erwünscht.", "donation"), ("donation", 1))

    def test_uncertain_or_signal_free_material_has_no_answer(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            self.assertEqual(self.ask(connection, "Frei ab 12 Jahren.", "free", 0.7), (None, 1))
            self.assertEqual(self.ask(connection, "Tischgebühr 7 Euro.", "vendor_only"), ("vendor_only", 1))
            self.assertEqual(self.ask(connection, "Eine Lesung für Kinder."), (None, 0))

    def test_publication_fills_only_unknown_admission_and_revalidates(self):
        unknown = validate_event(make_event(description="Lesung in der Bücherei."))
        unstated = validate_event(make_event(title="Vortrag", description="Vortrag im Rathaus."))
        priced = validate_event(make_event(title="Konzert", description="Karten 12 Euro.", price="12 €"))
        events = [unknown, unstated, priced]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(ai_enrichment, "settings_from_env", return_value=ai_enrichment.AISettings(
                    enabled=True, api_key="", model="m", cache_db=Path(tmp) / "c.sqlite3",
                    jev_enabled=True, jev_api_key="k")), \
                patch.object(ai_decisions, "resolve_admission", side_effect=["free", "not_stated"]) as resolve:
            publication_enrichment._resolve_unknown_admission(events, {})
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(events[0].admission["isFree"], True)
        self.assertEqual(events[0].admission["basis"], "structured")
        self.assertFalse(events[0].admission_checked)
        self.assertIsNone(events[1].admission["isFree"])
        self.assertTrue(events[1].admission_checked)
        self.assertIs(events[2], priced)
        # A later known price makes the review marker meaningless.
        self.assertFalse(validate_event({**events[1].to_dict(), "price": "5 €"}).admission_checked)
