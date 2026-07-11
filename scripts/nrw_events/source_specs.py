"""Declarative specifications and adapters for standard sources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from . import common
from .models import RawEvent


class AdapterType(str, Enum):
    ICAL = "ical"
    JSON_LD = "json_ld"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    id: str
    display_name: str
    urls: tuple[str, ...]
    adapter: AdapterType
    city: str
    category_hint: str = ""
    trust: float = 1.0
    timeout: int = 25
    headers: tuple[tuple[str, str], ...] = ()
    critical: bool = False
    optional: bool = False


def adapter_for(spec: SourceSpec) -> Callable[[], list[RawEvent]]:
    if spec.adapter is AdapterType.ICAL:
        return lambda: common.fetch_ical(spec.urls[0], spec.display_name, spec.city,
                                         spec.category_hint, spec.trust)
    if spec.adapter is AdapterType.JSON_LD:
        def fetch_json_ld() -> list[RawEvent]:
            document = common.fetch_url(spec.urls[0], timeout=spec.timeout,
                                        headers=dict(spec.headers) or None)
            return common.events_from_jsonld(document, spec.display_name, spec.city,
                                             spec.category_hint, spec.trust, spec.urls[0])
        return fetch_json_ld
    raise ValueError(f"unsupported source adapter: {spec.adapter}")

