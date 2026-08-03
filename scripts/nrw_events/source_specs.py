"""Schema-validated declarative source registry and standard adapters."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from . import common
from .models import AdmissionDefault, RawEvent, normalize_source_id


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
    selectors = dict(spec.selectors)
    item_pattern = selectors.pop("item", "")
    if not item_pattern:
        raise ValueError(f"HTML source {spec.id} requires an item selector")
    events: list[RawEvent] = []
    for item in re.findall(item_pattern, document, flags=re.IGNORECASE | re.DOTALL):
        card = item if isinstance(item, str) else " ".join(item)
        fields = {}
        for name, pattern in selectors.items():
            match = re.search(pattern, card, flags=re.IGNORECASE | re.DOTALL)
            fields[name] = common.clean_html(match.group(1)) if match else ""
        parsed = common.parse_date(fields.get("date", ""))
        if not parsed:
            continue
        event = common.make_event(
            fields.get("title", ""),
            parsed,
            None,
            fields.get("venue", ""),
            fields.get("city", "") or spec.city,
            fields.get("description", ""),
            fields.get("link", "") or source_url,
            spec.display_name,
            spec.category_hint,
            spec.trust,
            source_id=spec.id,
            default_category_key=spec.default_category_key,
            category_locked=spec.category_locked,
        )
        if event:
            events.append(event)
    return events


def adapter_for(spec: SourceSpec) -> Callable[[], list[RawEvent]]:
    if spec.adapter is AdapterType.PYTHON:
        return _python_callable(spec.callable)

    def fetch() -> list[RawEvent]:
        events: list[RawEvent] = []
        urls = (*spec.urls, *spec.page_urls)
        for url in urls:
            if spec.adapter is AdapterType.ICAL:
                events.extend(
                    common.fetch_ical(
                        url,
                        spec.display_name,
                        spec.city,
                        spec.category_hint,
                        spec.trust,
                        spec.id,
                        admission=spec.admission,
                        default_category_key=spec.default_category_key,
                        category_locked=spec.category_locked,
                    )
                )
                continue
            document = common.fetch_url(
                url,
                timeout=spec.timeout,
                headers=dict(spec.headers) or None,
            )
            if spec.adapter is AdapterType.JSON_LD:
                events.extend(
                    common.events_from_jsonld(
                        document,
                        spec.display_name,
                        spec.city,
                        spec.category_hint,
                        spec.trust,
                        url,
                        spec.id,
                        admission=spec.admission,
                        default_category_key=spec.default_category_key,
                        category_locked=spec.category_locked,
                    )
                )
            elif spec.adapter is AdapterType.HTML:
                events.extend(_html_events(document, spec, url))
        # Optional static detail endpoints use the shared persistent TTL cache.
        for url in spec.detail_urls:
            document = common.fetch_detail_url(
                url,
                cache_namespace=f"source-spec-{spec.id}",
                timeout=spec.timeout,
                headers=dict(spec.headers) or None,
            )
            events.extend(
                common.events_from_jsonld(
                    document,
                    spec.display_name,
                    spec.city,
                    spec.category_hint,
                    spec.trust,
                    url,
                    spec.id,
                    admission=spec.admission,
                    default_category_key=spec.default_category_key,
                    category_locked=spec.category_locked,
                )
            )
        return events

    return fetch


def load_source_specs(path: Path) -> tuple[SourceSpec, ...]:
    """Load and validate the versioned registry before importing any source."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("sources"), list):
        raise ValueError("source registry must use schema_version 1 and a sources array")
    specs = []
    ids: set[str] = set()
    names: set[str] = set()
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
        spec = SourceSpec(
            id=source_id,
            display_name=display_name,
            urls=urls,
            adapter=adapter,
            city=str(raw.get("city") or ""),
            region=region,
            category_hint=str(raw.get("category_hint") or ""),
            trust=float(raw.get("trust", 1.0)),
            timeout=int(raw.get("timeout", 25)),
            headers=tuple(tuple(value) for value in raw.get("headers") or []),
            admission=AdmissionDefault(admission) if admission else None,
            default_category_key=str(raw.get("default_category_key") or ""),
            category_locked=bool(raw.get("category_locked", False)),
            callable=callable_reference,
            page_urls=page_urls,
            detail_urls=detail_urls,
            selectors=tuple(tuple(value) for value in raw.get("selectors") or []),
        )
        ids.add(source_id)
        names.add(display_name)
        specs.append(spec)
    return tuple(specs)
