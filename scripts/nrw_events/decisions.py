"""Validated, server-side Jev Decisions API; independent of event publication."""
from __future__ import annotations

import copy
import email.utils
import http.client
import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Any

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MAX_INPUT_BYTES = 32_000


class DecisionError(Exception):
    """Safe error: never retains provider bodies, credentials or input."""

    def __init__(self, code: str, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(f"Decisions request failed: {code}" + (f" (HTTP {status})" if status else ""))


def _check(condition: bool) -> None:
    if not condition:
        raise ValueError("Invalid Decisions payload")


def _number(value: Any, low: float, high: float) -> None:
    _check(type(value) in (int, float) and math.isfinite(value) and low <= value <= high)


def _text(value: Any) -> None:
    _check(isinstance(value, str) and bool(value.strip()))


def _exact(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    _check(type(value) is dict and required <= value.keys() <= required | (optional or set()))


def _json(value: Any, depth: int = 0, ancestors: set[int] | None = None) -> None:
    _check(depth <= 64)
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        _check(len(value) <= MAX_INPUT_BYTES)
        return
    if type(value) in (int, float):
        _check(math.isfinite(value))
        return
    _check(type(value) in (list, dict))
    ancestors = set() if ancestors is None else ancestors
    _check(id(value) not in ancestors and len(value) <= MAX_INPUT_BYTES)
    ancestors.add(id(value))
    if type(value) is dict:
        for key in value:
            _check(type(key) is str and len(key) <= MAX_INPUT_BYTES)
    for child in value.values() if type(value) is dict else value:
        _json(child, depth + 1, ancestors)
    ancestors.remove(id(value))


def _description(value: Any) -> None:
    if type(value) is str:
        _text(value)
    else:
        _check(value is None or type(value) in (list, dict))
        _json(value)


def validate_questions(questions: Any) -> None:
    _check(type(questions) is dict and 1 <= len(questions) <= 255)
    for key, question in questions.items():
        _text(key)
        _check(type(question) is dict)
        kind = question.get("type")
        _exact(question, {"type", "instructions"}, {"criteria"})
        _description(question["instructions"])
        criteria = question.get("criteria")
        if kind == "noul":
            if "criteria" in question:
                _exact(criteria, {"true", "false"})
                for item in criteria.values():
                    _description(item)
        elif kind == "choice":
            _check(type(criteria) is dict and 2 <= len(criteria) <= 255)
            for option, item in criteria.items():
                _text(option)
                _description(item)
        else:
            _check(kind == "score" and type(criteria) is list and 2 <= len(criteria) <= 10)
            for item in criteria:
                _description(item)


def validate_result(value: Any, questions: dict[str, Any]) -> dict[str, Any]:
    _check(type(value) is dict)
    _text(value.get("model"))
    answers = value.get("answers")
    _exact(answers, set(questions))
    for key, question in questions.items():
        answer = answers[key]
        kind = question["type"]
        _check(type(answer) is dict and answer.get("type") == kind)
        if kind == "noul":
            _exact(answer, {"type", "noul"})
            _number(answer["noul"], 0, 1)
            continue
        _exact(answer, {"type", kind, "probabilities", "confidence"} | ({"legend"} if kind == "score" else set()))
        _number(answer["confidence"], 0, 1)
        criteria = question["criteria"]
        keys = set(criteria) if kind == "choice" else {str(i) for i in range(len(criteria))}
        probabilities = answer["probabilities"]
        _exact(probabilities, keys)
        for probability in probabilities.values():
            _number(probability, 0, 1)
        _check(abs(sum(probabilities.values()) - 1) <= 0.001)
        if kind == "choice":
            _text(answer["choice"])
            _check(answer["choice"] in keys and probabilities[answer["choice"]] == max(probabilities.values()))
        else:
            _number(answer["score"], 0, len(criteria) - 1)
            _exact(answer["legend"], keys)
            # Serialized comparison distinguishes booleans from numeric rubric values.
            _check(json.dumps(answer["legend"], sort_keys=True) == json.dumps(dict(enumerate(criteria)), sort_keys=True))
    result = {"answers": copy.deepcopy(answers), "model": value["model"]}
    if "usage" in value:
        usage = value["usage"]
        _check(type(usage) is dict)
        for key in ("input_tokens", "output_tokens"):
            _number(usage.get(key), 0, 2**53 - 1)
            _check(type(usage[key]) is int)
        result["usage"] = {key: usage[key] for key in ("input_tokens", "output_tokens")}
        if "cost" in usage:
            _number(usage["cost"], 0, float("inf"))
            result["usage"]["cost"] = usage["cost"]
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenRouterDecisionClient:
    """Same key/model precedence and bounded retries as the TypeScript client."""

    def __init__(self, *, api_key: str | None = None, model: str | None = None,
                 timeout_ms: int = 30_000, max_retries: int = 1):
        self._key = api_key.strip() if api_key is not None else (
            os.environ.get("JEV_OPENROUTER_API_KEY", "").strip() or os.environ.get("OPENROUTER_API_KEY", "").strip())
        self._model = model.strip() if model is not None else (
            os.environ.get("JEV_OPENROUTER_MODEL", "").strip() or "typesafe/jev-1.13")
        if (not self._key or not self._model or any(c in self._key for c in "\r\n")
                or type(timeout_ms) is not int or not 1 <= timeout_ms <= 300_000
                or type(max_retries) is not int or not 0 <= max_retries <= 3):
            raise DecisionError("configuration")
        self._timeout = timeout_ms / 1000
        self._retries = max_retries
        self._opener = urllib.request.build_opener(_NoRedirect)

    def evaluate(self, *, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + self._timeout
        try:
            _json({"state": state, "questions": questions})
            validate_questions(questions)
            questions = copy.deepcopy(questions)
            body = json.dumps({"model": self._model, "state": state, "questions": questions},
                              ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise DecisionError("input") from None
        if len(body) > MAX_INPUT_BYTES:
            raise DecisionError("input_too_large")
        for attempt in range(self._retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DecisionError("timeout")
            request = urllib.request.Request(ENDPOINT, data=body, headers={
                "Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
            delay = 0.25 * 2**attempt
            failure = "transport"
            status = None
            try:
                with self._opener.open(request, timeout=remaining) as response:
                    raw = response.read(MAX_INPUT_BYTES * 16 + 1)
                if time.monotonic() >= deadline:
                    raise DecisionError("timeout")
                try:
                    _check(len(raw) <= MAX_INPUT_BYTES * 16)
                    return validate_result(json.loads(raw), questions)
                except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
                    raise DecisionError("response") from None
            except urllib.error.HTTPError as error:
                status = error.code
                retry_after = error.headers.get("Retry-After")
                error.close()
                if status not in (408, 429) and status < 500:
                    raise DecisionError("http", status) from None
                failure = "http"
                if retry_after:
                    try:
                        delay = float(retry_after)
                        if not math.isfinite(delay) or delay < 0:
                            raise ValueError
                    except ValueError:
                        try:
                            delay = max(0, email.utils.parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (ValueError, TypeError, OverflowError):
                            delay = 0.25 * 2**attempt
            except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
                if time.monotonic() >= deadline:
                    raise DecisionError("timeout") from None
            if attempt == self._retries:
                raise DecisionError(failure, status) from None
            if delay >= deadline - time.monotonic():
                raise DecisionError("timeout")
            time.sleep(delay)
        raise DecisionError("transport")
