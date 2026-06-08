"""
Deduplication, ranking, and Markdown report rendering.

Pure presentation + post-processing. No network, no source-specific logic.
"""

import os
import re

from . import common


# ── Dedup ───────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """Aggressively normalize a title for near-duplicate comparison."""
    t = (title or "").lower().strip()
    t = re.sub(r"^(ausstellung[:\s]*|exhibition[:\s]*|konzert[:\s]*|concert[:\s]*|tickets?\s+für\s+)", "", t)
    return re.sub(r"[^a-zäöüß0-9]", "", t)


def _dedup_key(ev: dict) -> str:
    """Stable key for dedup: normalized-title prefix + city prefix."""
    norm = normalize_title(ev.get("title", ""))
    return norm[:50] + "|" + (ev.get("city", "")).lower()[:10]


def deduplicate(events: list) -> list:
    """Collapse duplicates by fuzzy title+city, keeping the highest-scored copy."""
    best: dict = {}
    for ev in events:
        key = _dedup_key(ev)
        current = best.get(key)
        if current is None or ev["score"] > current["score"]:
            best[key] = ev
    # Preserve first-seen order for stability, but emit the winning copy per key.
    seen = set()
    result = []
    for ev in events:
        key = _dedup_key(ev)
        if key in seen:
            continue
        seen.add(key)
        result.append(best[key])
    return result


# ── Report rendering ────────────────────────────────────────────────

def _bucket(ev: dict) -> str:
    text = (ev.get("category", "") + " " + ev.get("title", "") + " " + ev.get("description", "")).lower()
    if any(k in text for k in ["techno", "electronic", "party", "dj", "nightlife"]) or re.search(r"\bclub\b", text):
        return "Nightlife & Electronic"
    if any(k in text for k in ["concert", "konzert", "musik", "music", "live"]):
        return "Concerts & Live Music"
    if any(k in text for k in ["führung", "tour", "rundgang", "streetart", "kirschblüte", "antikmarkt",
                               "flohmarkt", "markt", "weinwanderung", "wanderung", "walk", "weinberg",
                               "winzer", "weingut", "ahrtal", "stadtteilfest", "straßenfest", "strassenfest",
                               "dorffest", "kirmes", "genussmeile", "weinmeile", "siebengebirge",
                               "kottenforst", "natur"]):
        return "Walks, Markets & Outdoor"
    if any(k in text for k in ["exhibition", "ausstellung", "museum", "gallery", "galerie", "art", "kunst"]):
        return "Exhibitions & Museums"
    if any(k in text for k in ["theater", "comedy", "vortrag", "lecture", "film", "kino", "reading",
                               "meetup", "gaming", "hacker", "opensource"]):
        return "Talks, Community & Culture"
    return "Other"


def _priority_bonus(ev: dict) -> float:
    text = (ev.get("title", "") + " " + ev.get("category", "") + " " + ev.get("description", "")).lower()
    bonus = 0.0
    if "flohmarkt" in text:
        bonus += 0.5
    if any(k in text for k in ["ahrweinwalk", "weinwanderung", "ahrtal", "ahrweiler"]):
        bonus += 0.55
    if any(k in text for k in ["stadtteilfest", "straßenfest", "strassenfest", "dorffest",
                               "poppelsdorf", "weinmeile", "genussmeile"]):
        bonus += 0.45
    if "antikmarkt" in text:
        bonus += 0.3
    if ev.get("city") == "Bonn":
        bonus += 0.1
    return bonus


PREFERRED_ORDER = [
    ("Nightlife & Electronic", "🌙"),
    ("Concerts & Live Music", "🎵"),
    ("Exhibitions & Museums", "🏛️"),
    ("Talks, Community & Culture", "🧠"),
    ("Walks, Markets & Outdoor", "🚶"),
    ("Other", "📌"),
]


def format_report(events: list) -> str:
    """Render the deduplicated, scored event list into a grouped Markdown report."""
    lines = [
        "# 🗓 Weekend Event Report",
        f"**{common.TODAY.strftime('%A %d %b')} → {common.END_DATE.strftime('%A %d %b %Y')}**",
        f"**Radius:** {common.MAX_RADIUS_KM}km from Bonn",
        f"**Sources:** {len(set(e['source'] for e in events))} active",
        f"**Relevant events after cleanup:** {len(events)}",
        "",
    ]

    grouped = {name: [] for name, _ in PREFERRED_ORDER}
    for ev in sorted(events, key=lambda x: (-(x["score"] + _priority_bonus(x)),
                                            x.get("distance_km", 999), x.get("title", ""))):
        grouped[_bucket(ev)].append(ev)

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
            dist_tag = f"{ev['distance_km']}km" if ev.get("distance_km", 0) > 0 else "Bonn"
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
