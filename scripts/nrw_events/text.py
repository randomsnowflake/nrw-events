"""Source-independent text, HTML, URL, and description helpers."""

from .core import (
    GeneratedDescription,
    clean_html,
    concise_description,
    description_source_for,
    factual_event_description,
    normalize_url,
    normalize_venue_name,
    sanitize_time_text,
)

__all__ = [
    "GeneratedDescription", "clean_html", "concise_description", "description_source_for",
    "factual_event_description",
    "normalize_url", "normalize_venue_name", "sanitize_time_text",
]
