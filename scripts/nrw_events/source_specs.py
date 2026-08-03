"""Schema-validated declarative source registry and standard adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import importlib
import inspect
import json
from pathlib import Path
import re
from typing import Callable
from urllib.parse import urljoin, urlsplit

from . import common
from .models import AdmissionDefault, RawEvent, normalize_source_id
from .health import SourceFetchResult


class AdapterType(str, Enum):
    ICAL = "ical"
    JSON_LD = "json_ld"
    HTML = "html"
    PYTHON = "python"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    id: str
    display_name: str
    urls: tuple[str, ...] = ()
    adapter: AdapterType = AdapterType.PYTHON
    city: str = ""
    category_hint: str = ""
    trust: float = 1.0
    timeout: int = 25
    headers: tuple[tuple[str, str], ...] = ()
    admission: AdmissionDefault | None = None
    default_category_key: str = ""
    category_locked: bool = False
    region: str = "bonn-region"
    callable: str = ""
    page_urls: tuple[str, ...] = ()
    detail_urls: tuple[str, ...] = ()
    selectors: tuple[tuple[str, str], ...] = ()
    component_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CardSpec:
    """Declarative extraction rules for a repeated HTML event card."""

    item: str
    fields: tuple[tuple[str, str], ...]
    source_id: str
    display_name: str
    city: str
    category_hint: str
    trust: float
    source_url: str
    default_category_key: str = ""
    category_locked: bool = False
    date_parser: Callable[[str], object] = common.parse_date

    @classmethod
    def from_source_spec(cls, spec: SourceSpec, source_url: str) -> "CardSpec":
        selectors = dict(spec.selectors)
        item = selectors.pop("item", "")
        if not item:
            raise ValueError(f"HTML source {spec.id} requires an item selector")
        return cls(
            item=item, fields=tuple(selectors.items()), source_id=spec.id,
            display_name=spec.display_name, city=spec.city,
            category_hint=spec.category_hint, trust=spec.trust,
            source_url=source_url, default_category_key=spec.default_category_key,
            category_locked=spec.category_locked,
        )


def events_from_cards(document: str, spec: CardSpec) -> list[RawEvent]:
    """Parse a repeated-card document through one shared extraction pipeline."""
    events: list[RawEvent] = []
    selectors = dict(spec.fields)
    for match in re.finditer(spec.item, document, flags=re.IGNORECASE | re.DOTALL):
        card = match.group(1) if match.lastindex else match.group(0)
        fields: dict[str, str] = {}
        for name, pattern in selectors.items():
            field_match = re.search(pattern, card, flags=re.IGNORECASE | re.DOTALL)
            fields[name] = common.clean_html(field_match.group(1)) if field_match else ""
        title = fields.get("title", "")
        parsed = spec.date_parser(fields.get("date", ""))
        if not title or not parsed:
            continue
        time_text = fields.get("time", "")
        canonical_time, _ = common.normalize_time_fields(time_text)
        time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", canonical_time)
        if isinstance(parsed, datetime) and time_match:
            parsed = parsed.replace(
                hour=int(time_match.group(1)), minute=int(time_match.group(2)),
            )
        end = spec.date_parser(fields.get("end_date", "")) if fields.get("end_date") else None
        event = common.make_event(
            title, parsed, end, fields.get("venue", ""),
            fields.get("city", "") or spec.city,
            fields.get("description", "") or common.clean_html(card),
            urljoin(spec.source_url, fields.get("link", "")) if fields.get("link") else spec.source_url,
            spec.display_name, fields.get("category", "") or spec.category_hint,
            spec.trust, time_text=time_text, source_id=spec.source_id,
            default_category_key=spec.default_category_key,
            category_locked=spec.category_locked,
        )
        if event:
            events.append(event)
    return events


def _python_callable(reference: str) -> Callable[[], list[RawEvent]]:
    module_name, separator, attribute_path = reference.partition(":")
    if not separator or not module_name.startswith("nrw_events.sources."):
        raise ValueError(f"invalid source callable: {reference!r}")
    target = importlib.import_module(module_name)
    for attribute in attribute_path.split("."):
        target = getattr(target, attribute)
    if not callable(target):
        raise ValueError(f"source callable is not callable: {reference!r}")
    return target


def _html_events(document: str, spec: SourceSpec, source_url: str) -> list[RawEvent]:
    """Parse regex-selector fixtures for simple card-shaped HTML sources."""
    return events_from_cards(document, CardSpec.from_source_spec(spec, source_url))


def adapter_for(spec: SourceSpec) -> Callable[[], list[RawEvent]]:
    if spec.adapter is AdapterType.PYTHON:
        return _python_callable(spec.callable)

    def fetch() -> list[RawEvent]:
        events: list[RawEvent] = []
        urls = (*spec.urls, *spec.page_urls)
        for url in urls:
            if spec.adapter is AdapterType.ICAL:
                events.extend(common.fetch_ical(
                    url, spec.display_name, spec.city, spec.category_hint,
                    spec.trust, spec.id, admission=spec.admission,
                    default_category_key=spec.default_category_key,
                    category_locked=spec.category_locked,
                ))
                continue
            document = common.fetch_url(
                url, timeout=spec.timeout, headers=dict(spec.headers) or None,
            )
            if spec.adapter is AdapterType.JSON_LD:
                events.extend(common.events_from_jsonld(
                    document, spec.display_name, spec.city, spec.category_hint,
                    spec.trust, url, spec.id, admission=spec.admission,
                    default_category_key=spec.default_category_key,
                    category_locked=spec.category_locked,
                ))
            elif spec.adapter is AdapterType.HTML:
                with common.capture_parser_metrics() as metrics:
                    parsed_events = _html_events(document, spec, url)
                parser_empty = not parsed_events and metrics["out_of_window_count"] == 0
                common._record_endpoint(
                    url, parser_type="html",
                    candidate_count=metrics["candidate_count"],
                    out_of_window_count=metrics["out_of_window_count"],
                    parsed_event_count=len(parsed_events), parser_empty=parser_empty,
                )
                events.extend(parsed_events)
        # Optional static detail endpoints use the shared persistent TTL cache.
        for url in spec.detail_urls:
            document = common.fetch_detail_url(
                url, cache_namespace=f"source-spec-{spec.id}", timeout=spec.timeout,
                headers=dict(spec.headers) or None,
            )
            events.extend(common.events_from_jsonld(
                document, spec.display_name, spec.city, spec.category_hint,
                spec.trust, url, spec.id, admission=spec.admission,
                default_category_key=spec.default_category_key,
                category_locked=spec.category_locked,
            ))
        return events

    return fetch


def typed_adapter_for(spec: SourceSpec) -> Callable[[], SourceFetchResult]:
    """Lift every legacy/declarative adapter into the typed source contract."""
    fetcher = adapter_for(spec)
    accepts_spec = (
        spec.adapter is AdapterType.PYTHON
        and len(inspect.signature(fetcher).parameters) == 1
    )

    def fetch() -> SourceFetchResult:
        result = fetcher(spec) if accepts_spec else fetcher()
        if isinstance(result, SourceFetchResult):
            return result
        if not isinstance(result, list):
            raise TypeError(f"source returned {type(result).__name__}, expected list or SourceFetchResult")
        return SourceFetchResult.success(result)

    return fetch


def load_source_specs(path: Path) -> tuple[SourceSpec, ...]:
    """Load and validate the versioned registry before importing any source."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("sources"), list):
        raise ValueError("source registry must use schema_version 1 and a sources array")
    specs = []
    ids: set[str] = set()
    names: set[str] = set()
    component_ids: set[str] = set()
    for index, raw in enumerate(payload["sources"]):
        if not isinstance(raw, dict):
            raise ValueError(f"source registry row {index} must be an object")
        source_id = str(raw.get("id") or "")
        display_name = str(raw.get("display_name") or "")
        region = str(raw.get("region") or "")
        if not source_id or source_id != normalize_source_id(source_id):
            raise ValueError(f"source registry row {index} has an invalid id")
        if not display_name or not region:
            raise ValueError(f"source registry row {index} requires display_name and region")
        if source_id in ids or display_name in names:
            raise ValueError(f"duplicate source registry identity: {source_id}/{display_name}")
        try:
            adapter = AdapterType(raw.get("adapter"))
        except ValueError as exc:
            raise ValueError(f"source registry row {index} has an invalid adapter") from exc
        urls = tuple(str(value) for value in raw.get("urls") or [])
        page_urls = tuple(str(value) for value in raw.get("page_urls") or [])
        detail_urls = tuple(str(value) for value in raw.get("detail_urls") or [])
        for url in (*urls, *page_urls, *detail_urls):
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"source registry row {index} has an invalid URL")
        callable_reference = str(raw.get("callable") or "")
        if adapter is AdapterType.PYTHON and not callable_reference:
            raise ValueError(f"Python source {source_id} requires callable")
        if adapter is not AdapterType.PYTHON and not urls:
            raise ValueError(f"declarative source {source_id} requires urls")
        admission = raw.get("admission")
        components = tuple(str(value) for value in raw.get("component_ids") or [])
        if any(
            not component or component != normalize_source_id(component)
            for component in components
        ):
            raise ValueError(f"source registry row {index} has an invalid component id")
        if len(components) != len(set(components)) or component_ids.intersection(components):
            raise ValueError(f"source registry row {index} has duplicate component ids")
        spec = SourceSpec(
            id=source_id, display_name=display_name, urls=urls, adapter=adapter,
            city=str(raw.get("city") or ""), region=region,
            category_hint=str(raw.get("category_hint") or ""),
            trust=float(raw.get("trust", 1.0)), timeout=int(raw.get("timeout", 25)),
            headers=tuple(tuple(value) for value in raw.get("headers") or []),
            admission=AdmissionDefault(admission) if admission else None,
            default_category_key=str(raw.get("default_category_key") or ""),
            category_locked=bool(raw.get("category_locked", False)),
            callable=callable_reference, page_urls=page_urls, detail_urls=detail_urls,
            selectors=tuple(tuple(value) for value in raw.get("selectors") or []),
            component_ids=components,
        )
        ids.add(source_id)
        names.add(display_name)
        component_ids.update(components)
        specs.append(spec)
    if ids.intersection(component_ids):
        raise ValueError("source registry component ids must not duplicate top-level source ids")
    return tuple(specs)
