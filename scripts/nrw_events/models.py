"""Typed contracts shared between source adapters and the import pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import re
from typing import Any, Iterator, Mapping, Optional, TypedDict


MAX_DISCOVERY_PROVENANCE_SOURCES = 20


class AdmissionDefault(str, Enum):
    """Conservative admission assumptions explicitly declared by a source."""

    FREE_BY_NATURE = "free_by_nature"
    SOURCE_CONFIRMED_FREE = "source_confirmed_free"


class RawEvent(TypedDict, total=False):
    title: str
    source: str
    source_id: str
    source_role: str
    discovered_via: list[str]
    date: str
    time: str
    identity_time: str
    identity_time_locked: bool
    time_note: str
    start_date: str
    end_date: str
    start_at: str
    end_at: str
    all_day: bool
    ongoing: bool
    timezone: str
    status: str
    venue: str
    identity_venue: str
    identity_venue_locked: bool
    venue_id: str
    venue_address: str
    venue_district: str
    venue_type: str
    venue_latitude: Optional[float]
    venue_longitude: Optional[float]
    city: str
    link: str
    link_kind: str
    source_links: list[str]
    previous_event_ids: list[str]
    organizer: str
    description: str
    description_html: str
    description_source: str
    ai_summary: str
    price: str
    admission_basis: str
    admission: dict[str, Any]
    availability: str
    category: str
    category_key: str
    category_label: str
    category_confidence: float
    category_reason: str
    distance_km: Optional[float]
    location_confidence: str
    location_source: str
    score: float
    ranking_features: dict[str, float]
    event_types: list[str]
    priority_bonus: float
    cancelled_at: str
    cancellation_source: str
    replacement_start_date: str
    first_seen_at: str
    content_hash: str
    series_id: str
    series_title: str
    run_id: str
    early_publication: bool
    daily_schedule: list[dict[str, str]]
    quality_warnings: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class EventDraft:
    """Source facts required to construct one normalized raw event."""

    title: str
    start: Optional[datetime]
    end: Optional[datetime]
    venue: str
    city: str
    description: str
    link: str
    source: str
    category: str
    trust: float = 1.0
    time_text: str = ""
    coords: Optional[tuple] = None
    all_day: Optional[bool] = None
    timezone_name: str = "Europe/Berlin"
    source_id: str = ""
    source_role: str = "primary"
    discovered_via: tuple[str, ...] = ()
    link_kind: str = ""
    description_source: str = ""
    admission: AdmissionDefault | None = None
    time_note: str = ""
    default_category_key: str = ""
    category_locked: bool = False


def _empty_admission() -> dict[str, Any]:
    return {
        "isFree": None,
        "amount": None,
        "currency": "EUR",
        "basis": "",
        "note": "",
        "donationSuggested": False,
    }


@dataclass(frozen=True, slots=True)
class CanonicalEvent(Mapping[str, Any]):
    """A validated, immutable event safe for downstream pipeline stages."""

    title: str
    source: str
    start_date: str
    score: float
    source_id: str = ""
    source_role: str = "primary"
    discovered_via: list[str] = field(default_factory=list)
    date: str = ""
    time: str = ""
    identity_time: str = ""
    identity_time_locked: bool = False
    time_note: str = ""
    end_date: str = ""
    start_at: str = ""
    end_at: str = ""
    all_day: bool = True
    ongoing: bool = False
    timezone: str = "Europe/Berlin"
    status: str = "scheduled"
    venue: str = ""
    identity_venue: str = ""
    identity_venue_locked: bool = False
    venue_id: str = ""
    venue_address: str = ""
    venue_district: str = ""
    venue_type: str = ""
    venue_latitude: Optional[float] = None
    venue_longitude: Optional[float] = None
    city: str = ""
    link: str = ""
    link_kind: str = ""
    source_links: list[str] = field(default_factory=list)
    previous_event_ids: list[str] = field(default_factory=list)
    organizer: str = ""
    description: str = ""
    """The same copy as the allowed HTML subset; see ``richtext``."""
    description_html: str = ""
    description_source: str = "scraped"
    ai_summary: str = ""
    price: str = ""
    admission_basis: str = ""
    admission: dict[str, Any] = field(default_factory=_empty_admission)
    availability: str = ""
    category: str = ""
    category_key: str = "other"
    category_label: str = "Other"
    category_confidence: float = 0.0
    category_reason: str = ""
    distance_km: Optional[float] = None
    location_confidence: str = "unresolved"
    location_source: str = ""
    ranking_features: Optional[dict[str, float]] = None
    event_types: list[str] = field(default_factory=list)
    priority_bonus: float = 0.0
    cancelled_at: str = ""
    cancellation_source: str = ""
    replacement_start_date: str = ""
    first_seen_at: str = ""
    content_hash: str = ""
    preserved_event_id: str = ""
    series_id: str = ""
    series_title: str = ""
    run_id: str = ""
    early_publication: bool = False
    daily_schedule: list[dict[str, str]] = field(default_factory=list)
    quality_warnings: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __iter__(self) -> Iterator[str]:
        return iter(self.__dataclass_fields__)

    def __len__(self) -> int:
        return len(self.__dataclass_fields__)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


# Source adapters migrate independently and may keep the historical annotation.
EventRecord = RawEvent


def normalize_source_id(value: object) -> str:
    """Return a stable machine key for one logical event source."""
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
