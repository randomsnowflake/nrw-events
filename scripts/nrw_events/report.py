"""
Deduplication, ranking, and Markdown report rendering.

Pure presentation + post-processing. No network, no source-specific logic.
"""

import os
import re
from dataclasses import replace
from datetime import date, datetime
from difflib import SequenceMatcher

from . import common
from .models import CanonicalEvent


# Kept separate from ``score``: score includes distance and topical relevance,
# while authority decides which publisher owns the canonical record.
_AGGREGATOR_SOURCE_MARKERS = (
    "bonn.jetzt", "eventbrite", "meetup", "radio bonn", "ruhr-guide",
)
_CIVIC_AGGREGATOR_SOURCE_MARKERS = ("bonn.de events", "bonn.de sports")
_SEARCH_SOURCE_MARKERS = ("exa search", "grok search")


def source_authority(source: str) -> int:
    """Rank direct/local publishers above aggregators and search discovery."""
    normalized = " ".join((source or "").casefold().split())
    if any(marker in normalized for marker in _SEARCH_SOURCE_MARKERS):
        return 0
    if any(marker in normalized for marker in _AGGREGATOR_SOURCE_MARKERS):
        return 1
    if any(marker in normalized for marker in _CIVIC_AGGREGATOR_SOURCE_MARKERS):
        return 2
    return 3


# ── Dedup ───────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """Aggressively normalize a title for near-duplicate comparison."""
    t = (title or "").lower().strip()
    t = re.sub(r"^(ausstellung[:\s]*|exhibition[:\s]*|konzert[:\s]*|concert[:\s]*|kostenloser\s+eintritt[:\s]*|eintritt\s+frei[:\s]*|tickets?\s+für\s+)", "", t)
    return re.sub(r"[^a-zäöüß0-9]", "", t)


def _dedup_key(ev: dict) -> str:
    """Occurrence key: recurring appointments on different dates must survive."""
    norm = normalize_title(ev.get("title", ""))
    city = re.sub(r"\s+", " ", (ev.get("city", "") or "").lower()).strip()
    start_date = ev.get("start_date") or (ev.get("date", "") or "").split("–", 1)[0]
    return "|".join((norm, city, str(start_date)))


def _normalized_city(value: str) -> str:
    city = re.sub(r"\s+", " ", (value or "").lower()).strip()
    city = re.sub(r"\s*\([^)]*\)\s*$", "", city)
    if city.startswith("bonn-") or city in {"rheinaue", "poppelsdorf"}:
        return "bonn"
    if city.startswith("köln-"):
        return "köln"
    return city


def _locations_compatible(left: dict, right: dict) -> bool:
    if _normalized_city(left.get("city", "")) == _normalized_city(right.get("city", "")):
        return True
    left_venue = normalize_title(left.get("venue", ""))
    right_venue = normalize_title(right.get("venue", ""))
    if left_venue and left_venue == right_venue:
        return True
    left_title = normalize_title(left.get("title", ""))
    right_title = normalize_title(right.get("title", ""))
    return (
        not left_venue
        and not right_venue
        and min(len(left_title), len(right_title)) >= 24
        and SequenceMatcher(None, left_title, right_title).ratio() >= 0.92
    )


def _date_bounds(ev: dict) -> tuple[date, date] | None:
    """Return the inclusive date interval represented by an event."""
    start_value = ev.get("start_date") or (ev.get("date", "") or "").split("–", 1)[0]
    end_value = ev.get("end_date") or start_value
    try:
        start = date.fromisoformat(str(start_value))
        end = date.fromisoformat(str(end_value))
    except ValueError:
        return None
    return (start, max(start, end))


def _same_occurrence(left: dict, right: dict) -> bool:
    """Return whether two records describe overlapping city/date occurrences."""
    left_bounds = _date_bounds(left)
    right_bounds = _date_bounds(right)
    if left_bounds and right_bounds:
        dates_match = (left_bounds[0] <= right_bounds[1]
                       and right_bounds[0] <= left_bounds[1])
    else:
        dates_match = (_dedup_key(left).rsplit("|", 1)[-1]
                       == _dedup_key(right).rsplit("|", 1)[-1])
    return dates_match and _locations_compatible(left, right)


def _duration_days(ev: dict) -> int:
    bounds = _date_bounds(ev)
    return (bounds[1] - bounds[0]).days if bounds else 0


def _titles_match(left: dict, right: dict) -> bool:
    """Match exact titles and very close cross-source title variants."""
    left_title = normalize_title(left.get("title", ""))
    right_title = normalize_title(right.get("title", ""))
    if left_title == right_title:
        return True
    if min(len(left_title), len(right_title)) >= 12 and (
        left_title in right_title or right_title in left_title
    ):
        return True
    return SequenceMatcher(None, left_title, right_title).ratio() >= 0.88


def _merge_duplicate_metadata(winner, duplicate):
    """Keep the authoritative record and enrich it field by field."""
    updates = {}
    for field in ("price", "venue", "time", "start_at", "end_at"):
        if not winner.get(field) and duplicate.get(field):
            updates[field] = duplicate[field]

    winner_link = winner.get("link", "")
    duplicate_link = duplicate.get("link", "")
    if (not winner_link and duplicate_link) or (
        _is_radio_aggregation_link(winner_link)
        and duplicate_link
        and not _is_radio_aggregation_link(duplicate_link)
    ):
        updates["link"] = duplicate_link

    if len(duplicate.get("description", "").strip()) > len(winner.get("description", "").strip()):
        updates["description"] = duplicate["description"]

    # Classification is derived data, so retain the most confident result even
    # when it did not come from the canonical publisher.
    if (duplicate.get("category_key")
            and duplicate.get("category_confidence", 0) > winner.get("category_confidence", 0)):
        for field in ("category", "category_key", "category_label", "category_confidence", "category_reason"):
            if duplicate.get(field):
                updates[field] = duplicate[field]

    if isinstance(winner, CanonicalEvent):
        return replace(winner, **updates)
    return {**winner, **updates}


def _is_radio_aggregation_link(link: str) -> bool:
    parsed = common.urllib.parse.urlsplit(link or "")
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    return (
        hostname == "radiobonn.de"
        and parsed.path.rstrip("/")
        == "/artikel/was-geht-unsere-veranstaltungstipps-2674962"
    )


def deduplicate(events: list[CanonicalEvent]) -> list[CanonicalEvent]:
    """Collapse duplicates, preferring source authority and then event score."""
    result: list = []
    for ev in events:
        match_index = next(
            (
                index
                for index in range(len(result))
                if _same_occurrence(result[index], ev) and _titles_match(result[index], ev)
            ),
            None,
        )
        if match_index is None:
            result.append(ev)
            continue

        current = result[match_index]
        current_rank = (source_authority(current.get("source", "")), current["score"],
                        _duration_days(current))
        candidate_rank = (source_authority(ev.get("source", "")), ev["score"],
                          _duration_days(ev))
        result[match_index] = (_merge_duplicate_metadata(ev, current)
                               if candidate_rank > current_rank
                               else _merge_duplicate_metadata(current, ev))
    # Once a direct publisher owns a recognizable series, suppress civic or
    # commercial calendar copies of its other occurrences in this report
    # window. Equally authoritative records on different dates still survive.
    return [
        event for event in result
        if not any(
            source_authority(owner.get("source", "")) > source_authority(event.get("source", ""))
            and _titles_match(owner, event)
            and _locations_compatible(owner, event)
            for owner in result
        )
    ]


# ── Report rendering ────────────────────────────────────────────────

CATEGORY_SECTIONS = {
    "nightlife": "Nightlife & Electronic",
    "concert": "Concerts & Live Music",
    "exhibition": "Exhibitions & Museums",
    "stage": "Talks, Community & Culture", "cinema": "Talks, Community & Culture",
    "talk": "Talks, Community & Culture", "workshop": "Talks, Community & Culture",
    "kids": "Talks, Community & Culture", "sports": "Talks, Community & Culture",
    "festival": "Walks, Markets & Outdoor", "market": "Walks, Markets & Outdoor",
    "food": "Walks, Markets & Outdoor", "outdoor": "Walks, Markets & Outdoor",
    "other": "Other",
}


def _bucket(ev: dict) -> str:
    """Map the already-canonical category to exactly one report section."""
    return CATEGORY_SECTIONS[ev.get("category_key", "other")]


def ranking_features(ev: dict) -> dict[str, float]:
    """Return named editorial ranking features without presentation side effects."""
    text = (ev.get("title", "") + " " + ev.get("category", "") + " " + ev.get("description", "")).lower()
    features = {}
    if "flohmarkt" in text:
        features["flea_market"] = 0.5
    if any(k in text for k in ["ahrweinwalk", "weinwanderung", "ahrtal", "ahrweiler"]):
        features["ahr_wine"] = 0.55
    if any(k in text for k in ["stadtteilfest", "straßenfest", "strassenfest", "dorffest",
                               "poppelsdorf", "weinmeile", "genussmeile"]):
        features["local_festival"] = 0.45
    if "antikmarkt" in text:
        features["antique_market"] = 0.3
    if ev.get("city") == "Bonn":
        features["bonn_local"] = 0.1
    return features


def _priority_bonus(ev: dict) -> float:
    return sum(ranking_features(ev).values())


PREFERRED_ORDER = [
    ("Nightlife & Electronic", "🌙"),
    ("Concerts & Live Music", "🎵"),
    ("Exhibitions & Museums", "🏛️"),
    ("Talks, Community & Culture", "🧠"),
    ("Walks, Markets & Outdoor", "🚶"),
    ("Other", "📌"),
]


def format_report(events: list, *, window_start: datetime | None = None,
                  window_end: datetime | None = None, max_per_section: int | None = None) -> str:
    """Render the deduplicated, scored event list into a grouped Markdown report."""
    start = window_start or common.TODAY
    end = window_end or common.END_DATE
    lines = [
        "# 🗓 Weekend Event Report",
        f"**{start.strftime('%A %d %b')} → {end.strftime('%A %d %b %Y')}**",
        f"**Radius:** {common.MAX_RADIUS_KM}km from Bonn",
        f"**Sources:** {len(set(e['source'] for e in events))} active",
        f"**Relevant events after cleanup:** {len(events)}",
        "",
    ]

    grouped = {name: [] for name, _ in PREFERRED_ORDER}
    for ev in sorted(events, key=lambda x: (-(x["score"] + _priority_bonus(x)),
                                            x.get("distance_km") if x.get("distance_km") is not None else 999,
                                            x.get("title", ""))):
        grouped[_bucket(ev)].append(ev)

    if max_per_section is None:
        try:
            max_per_section = int(os.environ.get("NRW_EVENTS_MAX_PER_SECTION", "0"))
        except ValueError:
            max_per_section = 0

    def format_when(ev: dict) -> str:
        parts = []
        if ev.get("date"):
            parts.append(ev["date"])
        if ev.get("time"):
            parts.append(ev["time"])
        return " ".join(parts).strip()

    def format_section(title: str, emoji: str, items: list):
        if not items:
            return
        shown = items if max_per_section <= 0 else items[:max_per_section]
        count_note = f" ({len(items)})" if len(shown) == len(items) else f" ({len(shown)} of {len(items)})"
        lines.append(f"## {emoji} {title}{count_note}")
        lines.append("")
        for ev in shown:
            when = format_when(ev)
            distance = ev.get("distance_km")
            dist_tag = f"{distance}km" if distance and distance > 0 else (
                "Bonn" if distance == 0 else "Ort nicht aufgelöst"
            )
            score_bar = "★" * max(1, min(5, int(round(ev["score"] * 3))))
            meta = []
            if when:
                meta.append(when)
            if ev.get("venue"):
                meta.append(ev["venue"])
            if ev.get("city"):
                meta.append(ev["city"])
            meta.append(dist_tag)
            meta.append(score_bar)
            lines.append(f"- **{ev['title']}**")
            lines.append(f"  {' · '.join(meta)}")
            if ev.get("description"):
                lines.append(f"  _{ev['description']}_")
            if ev.get("link"):
                lines.append(f"  🔗 {ev['link']}")
            lines.append("")

    for name, emoji in PREFERRED_ORDER:
        format_section(name, emoji, grouped[name])

    lines.append("---")
    lines.append("### Source Status")
    source_counts = {}
    for e in events:
        source_counts[e["source"]] = source_counts.get(e["source"], 0) + 1
    for src, count in sorted(source_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {src}: {count} events")

    return "\n".join(lines)
