"""Offline contract and safe-failure tests for Jev."""
import copy
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from nrw_events.decisions import DecisionError, OpenRouterDecisionClient, validate_result

QUESTIONS = {"music": {"type": "noul", "instructions": "Music event?"}}
RESULT = {"model": "resolved", "answers": {"music": {"type": "noul", "noul": 0.95}}}


class DecisionsTest(unittest.TestCase):
    def client(self, **kwargs):
        return OpenRouterDecisionClient(api_key="synthetic-key", **kwargs)

    def test_success(self):
        client = self.client()
        with patch.object(client._opener, "open", return_value=io.BytesIO(json.dumps(RESULT).encode())) as send:
            self.assertEqual(client.evaluate(state={}, questions=QUESTIONS), RESULT)
            self.assertEqual(send.call_args.args[0].full_url, "https://openrouter.ai/api/alpha/decisions")

    def test_bad_inputs_never_call_provider(self):
        for state in (float("nan"), {1: "numeric key"}, object(), "x" * 32001):
            client = self.client()
            with self.subTest(state=type(state)), patch.object(client._opener, "open") as send:
                with self.assertRaises(DecisionError):
                    client.evaluate(state=state, questions=QUESTIONS)
                send.assert_not_called()

    def test_invalid_responses(self):
        for probability in (-1, 1.1, True, float("nan")):
            result = copy.deepcopy(RESULT)
            result["answers"]["music"]["noul"] = probability
            with self.assertRaises(ValueError):
                validate_result(result, QUESTIONS)

    def test_choice_and_score(self):
        for kind, criteria, fields in (
            ("choice", {"0": "no", "1": "yes"}, {"choice": "1"}),
            ("score", ["no", "yes"], {"score": 0.9, "legend": {"0": "no", "1": "yes"}}),
        ):
            questions = {"q": {"type": kind, "instructions": None, "criteria": criteria}}
            value = {"model": "resolved", "answers": {"q": {"type": kind, **fields,
                     "confidence": 0.9, "probabilities": {"0": 0.1, "1": 0.9}}}}
            self.assertEqual(validate_result(value, questions), value)
            value["answers"]["q"]["probabilities"]["0"] = 0.5
            with self.assertRaises(ValueError):
                validate_result(value, questions)

    def test_safe_http_error_and_retry(self):
        for status, calls in ((400, 1), (503, 2)):
            client = self.client()
            with patch.object(client._opener, "open", side_effect=HTTPError("url", status, "secret", {}, None)) as send:
                with self.assertRaises(DecisionError) as caught:
                    client.evaluate(state={}, questions=QUESTIONS)
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(send.call_count, calls)

    def test_explicit_blank_key_does_not_fallback(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "shared"}):
            with self.assertRaises(DecisionError):
                OpenRouterDecisionClient(api_key=" ")

    def test_retry_after_respects_budget(self):
        client = self.client(timeout_ms=100)
        with patch.object(client._opener, "open", side_effect=HTTPError("url", 429, "", {"Retry-After": "10"}, None)) as send:
            with self.assertRaises(DecisionError) as caught:
                client.evaluate(state={}, questions=QUESTIONS)
            self.assertEqual(caught.exception.code, "timeout")
            self.assertEqual(send.call_count, 1)
