"""Owning implementation of markdown report; core is a compatibility facade."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from . import common
from . import ranking as _impl_ranking

CATEGORY_SECTIONS = {
    "nightlife": "Nightlife & Electronic",
    "concert": "Concerts & Live Music",
    "exhibition": "Exhibitions & Museums",
    "stage": "Talks, Community & Culture", "cinema": "Talks, Community & Culture",
    "talk": "Talks, Community & Culture", "workshop": "Talks, Community & Culture",
    "kids": "Talks, Community & Culture", "sports": "Talks, Community & Culture",
    "activities": "Talks, Community & Culture",
    "festival": "Walks, Markets & Outdoor", "market": "Walks, Markets & Outdoor",
    "food": "Walks, Markets & Outdoor", "outdoor": "Walks, Markets & Outdoor",
    "other": "Other",
}


def _bucket(ev: dict) -> str:
    """Map the already-canonical category to exactly one report section."""
    return CATEGORY_SECTIONS[ev.get("category_key", "other")]


def _escape_markdown(value: object) -> str:
    """Escape untrusted text for the inline Markdown contexts used below."""
    text = " ".join(str(value).split())
    for character in ("\\", "*", "_", "`", "[", "]", "#", "<", ">"):
        text = text.replace(character, f"\\{character}")
    return text


def _bounded_report(lines: list[str], max_chars: int, total_events: int) -> str:
    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    suffix_template = "\n\n… und {omitted} weitere Events (Ausgabe gekürzt)"
    budget = max(max_chars - len(suffix_template.format(omitted=total_events)), 0)
    prefix = rendered[:budget].rsplit("\n", 1)[0].rstrip()
    rendered_events = sum(line.startswith("- **") for line in prefix.splitlines())
    suffix = suffix_template.format(omitted=max(total_events - rendered_events, 0))
    while len(prefix) + len(suffix) > max_chars and "\n" in prefix:
        prefix = prefix.rsplit("\n", 1)[0].rstrip()
    return prefix + suffix


def format_report(events: list, *, window_start: datetime | None = None,
                  window_end: datetime | None = None, max_per_section: int = 0,
                  max_chars: int = 0, radius_km: float | None = None) -> str:
    """Render the deduplicated, scored event list into a grouped Markdown report."""
    start = window_start or common.runtime_window().start
    end = window_end or common.runtime_window().end
    lines = [
        "# 🗓 Weekend Event Report",
        f"**{start.strftime('%A %d %b')} → {end.strftime('%A %d %b %Y')}**",
        f"**Radius:** {common.runtime_radius_km() if radius_km is None else radius_km}km from Bonn",
        f"**Sources:** {len({e['source'] for e in events})} active",
        f"**Relevant events after cleanup:** {len(events)}",
        "",
    ]

    grouped: dict[str, list] = {name: [] for name, _ in _impl_ranking.PREFERRED_ORDER}
    for ev in sorted(events, key=lambda x: (-(x["score"] + _impl_ranking._priority_bonus(x)),
                                            x.get("distance_km") if x.get("distance_km") is not None else 999,
                                            x.get("title", ""))):
        grouped[_bucket(ev)].append(ev)

    def format_when(ev: dict) -> str:
        parts = []
        if ev.get("date"):
            parts.append(ev["date"])
        if ev.get("time"):
            parts.append(ev["time"])
        return " ".join(parts).strip()

    def format_section(title: str, emoji: str, items: list) -> None:
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
            score_bar = "★" * max(1, min(5, round(ev["score"] * 3)))
            meta = []
            if when:
                meta.append(when)
            if ev.get("venue"):
                meta.append(_escape_markdown(ev["venue"]))
            if ev.get("city"):
                meta.append(_escape_markdown(ev["city"]))
            meta.append(dist_tag)
            meta.append(score_bar)
            lines.append(f"- **{_escape_markdown(ev['title'])}**")
            lines.append(f"  {' · '.join(meta)}")
            if ev.get("description"):
                # One markdown list item per event: a real break here would end
                # the emphasis run and split the bullet.
                flat = " ".join(ev["description"].split())
                lines.append(f"  _{_escape_markdown(flat)}_")
            if ev.get("link"):
                link = str(ev["link"]).replace(">", "%3E").replace("<", "%3C")
                lines.append(f"  🔗 <{link}>")
            lines.append("")
        omitted = len(items) - len(shown)
        if omitted:
            lines.append(f"- … und {omitted} weitere")
            lines.append("")

    for name, emoji in _impl_ranking.PREFERRED_ORDER:
        format_section(name, emoji, grouped[name])

    lines.append("---")
    lines.append("### Source Status")
    source_counts: dict[str, int] = {}
    for e in events:
        source_counts[e["source"]] = source_counts.get(e["source"], 0) + 1
    for src, count in sorted(source_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {_escape_markdown(src)}: {count} events")

    uncategorized = [event for event in events if event.get("category_key") == "other"]
    unresolved = [
        event for event in events
        if event.get("location_confidence") == "unresolved"
    ]
    missing_venue = [event for event in events if not str(event.get("venue") or "").strip()]
    if uncategorized or unresolved or missing_venue:
        lines.extend(["", "### Ergänzungshinweise"])
    if uncategorized:
        examples = "; ".join(
            f"{_escape_markdown(event.get('title', 'Ohne Titel'))} ({_escape_markdown(event.get('source', 'unbekannte Quelle'))})"
            for event in uncategorized[:5]
        )
        lines.append(
            f"- Kategorie ergänzen: Termine auf Sonstiges: {len(uncategorized)}. Beispiele: {examples}."
        )
    if unresolved:
        examples = "; ".join(
            f"{_escape_markdown(event.get('title', 'Ohne Titel'))} ({_escape_markdown(event.get('city', 'Ort fehlt'))})"
            for event in unresolved[:5]
        )
        lines.append(
            f"- Ortschaft prüfen: geografisch nicht aufgelöste Termine: {len(unresolved)}. Beispiele: {examples}."
        )
    if missing_venue:
        counts = Counter(str(event.get("source") or "unbekannte Quelle") for event in missing_venue)
        sources = "; ".join(
            f"{_escape_markdown(source)} ({count})"
            for source, count in counts.most_common(5)
        )
        lines.append(
            f"- Veranstaltungsort ergänzen: Termine ohne Venue: {len(missing_venue)}; größte Lücken: {sources}."
        )

    return _bounded_report(lines, max_chars, len(events)) if max_chars > 0 else "\n".join(lines)
