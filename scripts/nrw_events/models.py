"""Typed contracts shared between source adapters and the import pipeline."""

from __future__ import annotations

from typing import Optional, TypedDict


class EventRecord(TypedDict, total=False):
    title: str
    source: str
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
