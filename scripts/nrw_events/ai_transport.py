"""Owning implementation of ai transport; core is a compatibility facade."""

from __future__ import annotations

import json
import multiprocessing
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any

from . import ai_contracts as _impl_ai_contracts


def _sleep_before_ai_retry(error: Exception, attempt: int, settings: AISettings) -> None:
    if not isinstance(error, _impl_ai_contracts.AIEnrichmentError) or not error.transient:
        return
    delay = error.retry_after if error.retry_after is not None else 2.0 ** attempt
    time.sleep(min(delay, settings.timeout_seconds))


def _read_bounded_response(
    response: Any,
    timeout_seconds: float,
    *,
    started: float | None = None,
) -> bytes:
    """Read an HTTP response incrementally with a wall-clock and size bound."""
    deadline = (started if started is not None else time.monotonic()) + timeout_seconds
    chunks: list[bytes] = []
    total = 0
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("AI request exceeded its wall-clock deadline")
        try:
            chunk = response.read(_impl_ai_contracts._RESPONSE_CHUNK_BYTES)
            single_read = False
        except TypeError:
            # Keep compatibility with small test/opening adapters that expose
            # the minimal no-argument file-like read contract.
            chunk = response.read()
            single_read = True
        if not chunk:
            break
        value = bytes(chunk)
        total += len(value)
        if total > _impl_ai_contracts._MAX_RESPONSE_BYTES:
            raise OSError(f"AI response exceeded {_impl_ai_contracts._MAX_RESPONSE_BYTES}-byte limit")
        chunks.append(value)
        if time.monotonic() >= deadline:
            raise TimeoutError("AI request exceeded its wall-clock deadline")
        if single_read:
            break
    return b"".join(chunks)


@dataclass(frozen=True, slots=True)
class AISettings:
    enabled: bool
    api_key: str
    model: str
    cache_db: Path
    provider: str = "openai"
    max_attempts: int = 2
    # Zero means permanent for this exact input hash and pipeline version.
    # A week lets a transient provider fault or a summary the local quality
    # gate rejected be retried instead of leaving only the master-data fallback.
    negative_cache_hours: float = 168.0
    timeout_seconds: float = 180.0
    # Bound the complete enrichment pass for one source. AI is optional
    # enrichment and must never keep the event import open indefinitely.
    # The budget must still cover a full first pass over the largest source:
    # restricted sources publish no source prose, so a skipped event otherwise
    # remains at the minimal master-data fallback. The runner reserves the same
    # amount on the source deadline, so raising this stays consistent.
    batch_timeout_seconds: float = 600.0
    # Independent events can safely use separate cache connections and API
    # requests. Keep this bounded so one large municipal source no longer
    # spends the complete batch budget processing records serially.
    workers: int = 8
    max_events: int = 0
    # Limit newly billable cache identities per UTC day and summary pipeline.
    # Zero is an explicit operator override for a deliberate full reprocess.
    max_new_cache_rows_per_day: int = 150
    facts_reasoning_effort: str = "none"
    summary_reasoning_effort: str = "none"
    # Keep ZDR by default. This can be relaxed explicitly for controlled
    # provider-routing experiments, but is not needed for normal operation.
    allow_data_collection: bool = False


def _isolated_http_worker(
    sender: Any,
    request_spec: Mapping[str, Any],
    socket_timeout: float,
) -> None:
    """Perform one HTTP request in a disposable process."""
    try:
        request = urllib.request.Request(
            str(request_spec["url"]),
            data=request_spec.get("data"),
            headers=dict(request_spec.get("headers") or {}),
            method=str(request_spec["method"]),
        )
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=socket_timeout) as response:
            sender.send(("ok", _read_bounded_response(response, socket_timeout, started=started)))
    except urllib.error.HTTPError as exc:
        sender.send(("http_error", exc.code, exc.headers.get("Retry-After", "")))
    except BaseException as exc:
        sender.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        sender.close()


def _read_response_isolated(
    request: urllib.request.Request,
    timeout_seconds: float,
    worker: Callable[..., None] = _isolated_http_worker,
) -> bytes:
    """Read one response with a killable wall-clock deadline in every thread."""
    if timeout_seconds <= 0:
        raise TimeoutError("AI request exceeded its wall-clock deadline")

    request_spec = {
        "url": request.full_url,
        "data": request.data,
        "headers": dict(request.header_items()),
        "method": request.get_method(),
    }
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=worker,
        args=(sender, request_spec, timeout_seconds),
        daemon=True,
    )
    started = time.monotonic()
    try:
        process.start()
        sender.close()
        remaining = max(timeout_seconds - (time.monotonic() - started), 0.0)
        if not receiver.poll(remaining):
            raise TimeoutError("AI request exceeded its wall-clock deadline")
        try:
            status, value, *details = receiver.recv()
        except EOFError as exc:
            raise OSError("AI request worker exited without a result") from exc
        if status == "ok":
            return bytes(value)
        if status == "http_error":
            headers = Message()
            if details and details[0]:
                headers["Retry-After"] = str(details[0])
            raise urllib.error.HTTPError(request.full_url, int(value), "", headers, None)
        raise OSError(f"AI request worker failed: {value}")
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
        process.close()


def _read_http_response(
    request: urllib.request.Request,
    timeout_seconds: float,
    opener: Callable[..., Any] | None,
) -> str:
    if opener is None:
        raw = _read_response_isolated(request, timeout_seconds)
    else:
        with opener(request, timeout=timeout_seconds) as response:
            raw = _read_bounded_response(response, timeout_seconds)
    return bytes(raw).decode("utf-8")


class ResponsesClient:
    """Small stdlib Responses API client with strict structured output."""

    def __init__(
        self,
        settings: AISettings,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._opener = opener

    def structured(self, *, stage: str, system: str, payload: Mapping[str, Any], schema: dict[str, Any], attempt: int) -> tuple[dict[str, Any], _impl_ai_contracts.Usage]:
        body = {
            "model": self.settings.model,
            "store": False,
            "reasoning": {
                "effort": (
                    self.settings.facts_reasoning_effort
                    if stage == "facts"
                    else self.settings.summary_reasoning_effort
                )
            },
            "max_output_tokens": (
                _impl_ai_contracts.FACTS_OUTPUT_TOKEN_LIMIT if stage == "facts" else _impl_ai_contracts.SUMMARY_OUTPUT_TOKEN_LIMIT
            ),
            "input": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"attempt": attempt, "event": payload},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": f"nrw_event_{stage}",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        request = urllib.request.Request(
            _impl_ai_contracts._OPENAI_API_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "nrw-events-ai-enrichment/1",
            },
            method="POST",
        )
        try:
            raw = _read_http_response(request, self.settings.timeout_seconds, self._opener)
        except urllib.error.HTTPError as exc:
            raise _impl_ai_contracts.AIEnrichmentError(
                f"OpenAI HTTP {exc.code}",
                transient=exc.code >= 500 or exc.code in {408, 429},
                retry_after=_impl_ai_contracts._retry_after_seconds(exc),
            ) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise _impl_ai_contracts.AIEnrichmentError(
                f"OpenAI request failed: {type(exc).__name__}: {exc}", transient=True
            ) from exc
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _impl_ai_contracts.AIEnrichmentError("OpenAI returned invalid JSON") from exc
        if document.get("status") != "completed":
            reason = (document.get("incomplete_details") or {}).get("reason") or document.get("status")
            raise _impl_ai_contracts.AIEnrichmentError(f"OpenAI response incomplete: {reason}")
        output_text = ""
        for output in document.get("output") or []:
            if output.get("type") != "message":
                continue
            for item in output.get("content") or []:
                if item.get("type") == "refusal":
                    raise _impl_ai_contracts.AIEnrichmentError("OpenAI refused the enrichment request")
                if item.get("type") == "output_text":
                    output_text += str(item.get("text") or "")
        if not output_text:
            raise _impl_ai_contracts.AIEnrichmentError("OpenAI response contained no output text")
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise _impl_ai_contracts.AIEnrichmentError("OpenAI structured output was not JSON") from exc
        if not isinstance(parsed, dict):
            raise _impl_ai_contracts.AIEnrichmentError("OpenAI structured output was not an object")
        _impl_ai_contracts._validate_types(schema, parsed)
        usage = document.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        return parsed, _impl_ai_contracts.Usage(
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(input_details.get("cached_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )


class OpenRouterClient:
    """OpenRouter Chat Completions client with strict JSON and ZDR routing."""

    def __init__(
        self,
        settings: AISettings,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._opener = opener

    def structured(self, *, stage: str, system: str, payload: Mapping[str, Any], schema: dict[str, Any], attempt: int) -> tuple[dict[str, Any], _impl_ai_contracts.Usage]:
        body = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"attempt": attempt, "event": payload},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
            "max_tokens": (
                _impl_ai_contracts.FACTS_OUTPUT_TOKEN_LIMIT if stage == "facts" else _impl_ai_contracts.SUMMARY_OUTPUT_TOKEN_LIMIT
            ),
            "reasoning": {
                "effort": (
                    self.settings.facts_reasoning_effort
                    if stage == "facts"
                    else self.settings.summary_reasoning_effort
                ),
                "exclude": True,
            },
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": f"nrw_event_{stage}",
                    "strict": True,
                    "schema": schema,
                },
            },
            "provider": (
                {
                    "require_parameters": True,
                    "data_collection": "allow",
                    "sort": "throughput",
                }
                if self.settings.allow_data_collection
                else {
                    "require_parameters": True,
                    "data_collection": "deny",
                    "zdr": True,
                    "sort": "throughput",
                }
            ),
        }
        request = urllib.request.Request(
            _impl_ai_contracts._OPENROUTER_API_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://veranstaltungen-bonn.de",
                "X-OpenRouter-Title": "nrw-events",
                "User-Agent": "nrw-events-ai-enrichment/1",
            },
            method="POST",
        )
        try:
            raw = _read_http_response(request, self.settings.timeout_seconds, self._opener)
        except urllib.error.HTTPError as exc:
            raise _impl_ai_contracts.AIEnrichmentError(
                f"OpenRouter HTTP {exc.code}",
                transient=exc.code >= 500 or exc.code in {408, 429},
                retry_after=_impl_ai_contracts._retry_after_seconds(exc),
            ) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise _impl_ai_contracts.AIEnrichmentError(
                f"OpenRouter request failed: {type(exc).__name__}: {exc}", transient=True
            ) from exc
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _impl_ai_contracts.AIEnrichmentError("OpenRouter returned invalid JSON") from exc
        usage = _openrouter_usage(document)
        if document.get("error"):
            error = document["error"] if isinstance(document["error"], dict) else {}
            raise _impl_ai_contracts.AIEnrichmentError(
                f"OpenRouter error: {str(error.get('message') or 'unknown')[:300]}",
                usage=usage,
            )
        choices = document.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise _impl_ai_contracts.AIEnrichmentError("OpenRouter response contained no choice", usage=usage)
        if choices[0].get("finish_reason") != "stop":
            raise _impl_ai_contracts.AIEnrichmentError(
                f"OpenRouter response incomplete: {choices[0].get('finish_reason')}",
                usage=usage,
            )
        message = choices[0].get("message") or {}
        output_text = message.get("content") if isinstance(message, dict) else ""
        if not isinstance(output_text, str) or not output_text:
            raise _impl_ai_contracts.AIEnrichmentError("OpenRouter response contained no output text", usage=usage)
        try:
            parsed = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise _impl_ai_contracts.AIEnrichmentError("OpenRouter structured output was not JSON", usage=usage) from exc
        if not isinstance(parsed, dict):
            raise _impl_ai_contracts.AIEnrichmentError("OpenRouter structured output was not an object", usage=usage)
        _impl_ai_contracts._validate_types(schema, parsed)
        return parsed, usage


def _openrouter_usage(document: Mapping[str, Any]) -> _impl_ai_contracts.Usage:
    raw = document.get("usage") or {}
    input_details = raw.get("prompt_tokens_details") or {}
    return _impl_ai_contracts.Usage(
        input_tokens=int(raw.get("prompt_tokens") or 0),
        cached_input_tokens=int(input_details.get("cached_tokens") or 0),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cost_usd=float(raw.get("cost") or 0),
    )
