"""Typed contracts shared between source adapters and the import pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterator, Mapping, Optional, TypedDict


class RawEvent(TypedDict, total=False):
    title: str
    source: str
    source_id: str
    date: str
    time: str
    start_date: str
    end_date: str
    start_at: str
    end_at: str
    all_day: bool
    timezone: str
    status: str
    venue: str
    city: str
    link: str
    description: str
    price: str
    category: str
    category_key: str
    category_label: str
    category_confidence: float
    category_reason: str
    distance_km: Optional[float]
    location_confidence: str
    location_source: str
    score: float


@dataclass(frozen=True, slots=True)
class CanonicalEvent(Mapping[str, Any]):
    """A validated, immutable event safe for downstream pipeline stages."""

    title: str
    source: str
    start_date: str
    score: float
    source_id: str = ""
    date: str = ""
    time: str = ""
    end_date: str = ""
    start_at: str = ""
    end_at: str = ""
    all_day: bool = True
    timezone: str = "Europe/Berlin"
    status: str = "scheduled"
    venue: str = ""
    city: str = ""
    link: str = ""
    description: str = ""
    price: str = ""
    category: str = ""
    category_key: str = "other"
    category_label: str = "Other"
    category_confidence: float = 0.0
    category_reason: str = ""
    distance_km: Optional[float] = None
    location_confidence: str = "unresolved"
    location_source: str = ""

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
