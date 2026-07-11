"""
Deduplication, ranking, and Markdown report rendering.

Pure presentation + post-processing. No network, no source-specific logic.
"""

import os
import re
from dataclasses import replace
from datetime import datetime
from difflib import SequenceMatcher

from . import common
from .models import CanonicalEvent


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


def _same_occurrence(left: dict, right: dict) -> bool:
    """Return whether two records describe the same city/date occurrence."""
    return _dedup_key(left).rsplit("|", 1)[-1] == _dedup_key(right).rsplit("|", 1)[-1] and (
        re.sub(r"\s+", " ", (left.get("city", "") or "").lower()).strip()
        == re.sub(r"\s+", " ", (right.get("city", "") or "").lower()).strip()
    )


def _titles_match(left: dict, right: dict) -> bool:
    """Match exact titles and very close cross-source title variants."""
    left_title = normalize_title(left.get("title", ""))
    right_title = normalize_title(right.get("title", ""))
    if left_title == right_title:
        return True
    return SequenceMatcher(None, left_title, right_title).ratio() >= 0.88


def _merge_duplicate_metadata(winner, duplicate):
    """Keep the winning event while preserving useful details from duplicates."""
    updates = {}
    for field in ("description", "price", "venue", "link", "time", "start_at", "end_at"):
        if not winner.get(field) and duplicate.get(field):
            updates[field] = duplicate[field]

    # If a lower-scored duplicate carries an explicit price/free-admission signal,
    # keep its stronger category metadata too. This prevents a broad duplicate
    # source from erasing a more structured municipal source such as Bonn.de JSON.
    if duplicate.get("price") and not winner.get("price") and duplicate.get("category_key"):
        for field in ("category", "category_key", "category_label", "category_confidence", "category_reason"):
            if duplicate.get(field):
                updates[field] = duplicate[field]

    if isinstance(winner, CanonicalEvent):
        return replace(winner, **updates)
    return {**winner, **updates}

def deduplicate(events: list[CanonicalEvent]) -> list[CanonicalEvent]:
    """Collapse same-day, same-city duplicates, keeping the highest-scored copy."""
    result: list = []
    occurrences: dict[str, list[int]] = {}
    for ev in events:
        key = _dedup_key(ev)
        occurrence_key = key.rsplit("|", 1)[-1] + "|" + re.sub(r"\s+", " ", (ev.get("city", "") or "").lower()).strip()
        match_index = next(
            (
                index
                for index in occurrences.get(occurrence_key, [])
                if _same_occurrence(result[index], ev) and _titles_match(result[index], ev)
            ),
            None,
        )
        if match_index is None:
            occurrences.setdefault(occurrence_key, []).append(len(result))
            result.append(ev)
            continue

        current = result[match_index]
        result[match_index] = _merge_duplicate_metadata(ev, current) if ev["score"] > current["score"] else _merge_duplicate_metadata(current, ev)
    return result


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
