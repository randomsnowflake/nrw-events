"""Prepare writer input from accepted facts without transport or cache access."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from . import ai_policy as _impl_ai_policy


@dataclass(frozen=True)
class WriterInput:
    facts: dict[str, Any]
    payload: dict[str, Any]


def prepare_writer_input(original: dict[str, Any], facts: dict[str, Any], payload: dict[str, Any]) -> WriterInput:
    writer_facts = {
        key: value for key, value in facts.items()
        if key not in {"is_concrete_event", "event_evidence"} and not key.startswith("_")
    }
    stage2_payload = {
        "facts": writer_facts,
        "existing_fields": {
            key: payload[key] for key in (
                "title", "start_date", "end_date", "time", "time_note", "venue",
                "venue_address", "city", "organizer", "price", "availability",
                "category_key", "series_title",
            )
        },
        "field_policy": {
            "locked_time": bool(original.get("time") or original.get("identity_time_locked")),
            "locked_venue": bool(original.get("venue") or original.get("identity_venue_locked")),
            "locked_admission": bool(
                original.get("admission_basis") == "explicit"
                or (
                    isinstance(original.get("admission"), dict)
                    and cast(dict, original["admission"]).get("basis") == "structured"
                )
            ),
            "admission_conflict": _impl_ai_policy._admission_conflicts(original, facts),
            "locked_category": bool(
                original.get("category_key") not in {None, "", "other"}
                and _impl_ai_policy._confidence(original.get("category_confidence")) >= 0.75
            ),
            "category_taxonomy": {
                "concert": "Live-Musik und Konzerte",
                "nightlife": "Partys, Clubs und Tanznächte; nicht bloß Singles als Zielgruppe",
                "stage": "Theater, Comedy, Tanzaufführungen und Bühne",
                "cinema": "Filmvorführungen und Kino",
                "exhibition": "Ausstellungen",
                "festival": "Feste und Stadtleben",
                "market": "Märkte und Flohmärkte",
                "food": "Essen, Trinken und Verkostungen",
                "outdoor": "Führungen, Spaziergänge, Radtouren und Wanderungen",
                "sports": "Sport, Training und Wettkämpfe",
                "talk": "Vorträge und Lesungen",
                "workshop": "Workshops und Kurse",
                "kids": "Angebote primär für Familien und Kinder",
                "activities": "Treffen und sonstige Aktivitäten",
                "other": "nur wenn keine passendere Kategorie gilt",
            },
        },
    }
    return WriterInput(writer_facts, stage2_payload)
