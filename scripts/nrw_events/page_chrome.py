"""Strip source-page furniture (calendar export buttons, map widgets, label/value blocks) from scraped copy.

Several CMS detail pages (WordPress event plugins, VHS course pages, municipal
calendars) are scraped as a whole. Their date/place/category blocks duplicate
the info box and their buttons ("Zum Kalender hinzufügen", "Kursdetails
drucken") are page chrome, not visitor copy. Prose and practical lines
(price, registration, meeting point, age) are kept.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from . import richtext

# Inline link hints that never belong in copy.
_INLINE = re.compile(r"\s*\((?:Öffnet|öffnet)\s+in\s+einem\s+neuen\s+(?:Tab|Fenster)\)|\s*Google\s+Karte\s+anzeigen")
# Only pages that show such furniture are rewritten line by line.
_MARKER = re.compile(
    r"Zum\s+Kalender\s+hinzufügen|ICS\s+herunterladen|iCalendar|Outlook\s+Live|Kursdetails\s+drucken|"
    r"Termine\s+als\s+iCal|Kartenansicht\s+zu\s+aktivieren|«\s*Alle\s+Veranstaltungen|Druckversion|"
    r"^\s*Date\(s\)\s+-|^\s*Veranstaltungstyp\s*$|^\s*Datum/Zeit\s*$|^\s*Kategorien\s*$",
    re.M,
)
_WIDGET_LINE = re.compile(
    r"^\s*(?:Zum\s+Kalender\s+hinzufügen|ICS\s+herunterladen|Google\s+Kalender|iCalendar|Office\s+365|Outlook\s+(?:365|Live)|"
    r"Kurs\s+in\s+den\s+Warenkorb\s+legen|Kursdetails\s+drucken|Termine\s+als\s+iCal-Datei|Kurs\s+weiterempfehlen|"
    r"zur\s+Anfahrtsbeschreibung|Großansicht\s+der\s+Karte\s+öffnen|Veranstalter-Website\s+anzeigen|Tickets\s+bestellen|"
    r"Facebook|Twitter|X|Whatsapp|WhatsApp|Instagram|Teilen|Drucken|Druckversion)\s*$"
    r"|Kartenansicht\s+zu\s+aktivieren|Nutzung\s+von\s+Google-Maps|zurück\s+zur\s+Übersicht|Zum\s+Kalender\s+hinzufügen"
    r"|^\s*«|»\s*$",
    re.I,
)
# Practical visitor facts survive even as short lines.
_KEEP = re.compile(r"€|\beuro\b|eintritt|kostenlos|kostenfrei|anmeld|treffpunkt|\bab\s+\d+\s+jahren|mitzubringen|barrierefrei|ermäßigt|vvk|abendkasse", re.I)
_LABELS = set(
    "datum zeit uhrzeit uhr ort wann wo veranstaltungsort kategorien kategorie veranstaltungstyp details plätze platz frei "
    "entgelt kursort kursnummer termine termin beginn einlass ende nur noch wenige wenig min max date s bis von am um "
    "vorverkaufsstellen preise quelle veranstaltungskalender karte nrw tickets".split()
)
_JOIN_LABELS = {"veranstalter", "dozenten", "dozent", "dozentin", "leitung", "referent", "referentin"}
_DROP_NEXT = {"telefon", "e-mail", "email", "fax", "website", "web"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß0-9]+", text.casefold())


def clean(text: str, known: str) -> str:
    """Return copy without page chrome; ``known`` holds title/venue/city words shown elsewhere."""
    text = _INLINE.sub("", text or "")
    if not _MARKER.search(text):
        return text
    known_words = set(_words(known)) | _LABELS
    blocks: list[list[str]] = []
    pending_label = ""
    skip_next = False
    for block in re.split(r"\n\s*\n", text):
        kept: list[str] = []
        for raw in block.split("\n"):
            line = raw.strip()
            if not line:
                continue
            if skip_next:
                skip_next = False
                continue
            bare = line.rstrip(":").strip().casefold()
            if bare in _DROP_NEXT:
                skip_next = True
                continue
            if bare in _JOIN_LABELS:
                pending_label = line.rstrip(":").strip()
                continue
            if pending_label:
                line, pending_label = f"{pending_label}: {line}", ""
            if _WIDGET_LINE.search(line):
                continue
            rest = [w for w in _words(line) if w not in known_words and not any(ch.isdigit() for ch in w)]
            if len(rest) < 4 and not _KEEP.search(line):
                continue
            kept.append(re.sub(r"\s+(?:Ort|Plätze|Entgelt|Datum|Zeit)\s*$", "", line))
        if kept:
            blocks.append(kept)
    return "\n\n".join("\n".join(lines) for lines in blocks)


def strip_page_chrome(event: dict[str, Any]) -> bool:
    """Clean description/description_html in place; True when the copy changed."""
    description = str(event.get("description") or "")
    html = str(event.get("description_html") or "")
    known = " ".join(str(event.get(k) or "") for k in ("title", "venue", "city", "venue_address", "organizer"))
    cleaned = clean(description, known)
    if cleaned == description:
        inline_html = _INLINE.sub("", html)
        if inline_html != html:
            event["description_html"] = inline_html
            return True
        return False
    event["description"] = cleaned
    event["description_html"] = richtext.from_plain_text(cleaned) if cleaned != _INLINE.sub("", description) or not html else _INLINE.sub("", html)
    return True


def needs_cleaning(event: Mapping[str, Any]) -> bool:
    return bool(_MARKER.search(str(event.get("description") or "")) or _INLINE.search(str(event.get("description") or "") + str(event.get("description_html") or "")))
