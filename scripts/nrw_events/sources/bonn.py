"""
Bonn.de — the city's official event channels.

Fetchers, all reading bonn.de:
  fetch_events()           — the server-rendered Veranstaltungskalender listing.
                             Primary source because it contains valid events that
                             the city JSON/RSS feeds omit or emit malformed.
  fetch_events_json()      — legacy JSON-only fallback for tests/manual probes.
  fetch_press_festivals()  — the annual "Veranstaltungsjahr" press release, which
                             lists district festivals / markets / Kirmes as <li>
                             items. This is the *live* replacement for the old
                             hardcoded district-festival table — no baked dates.
  fetch_sports()           — the public Sportveranstaltungen teaser page. This
                             exposes sport/active events that the primary JSON
                             source intentionally filters out.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from html import unescape

from .. import (
    category_taxonomy,
    common,
    detail_enrichment,
    reviewed_corrections,
    richtext,
)
from . import regional_common as rc

# Full official event calendar as structured JSON. This endpoint has repeatedly
# emitted malformed/truncated payloads and can miss entries visible in the public
# calendar, so it is kept only as a last-resort fallback/manual probe.
_EVENTS_JSON_URL = "https://www.bonn.de/citykey/events-json.php"

# Two open-data GeoJSON layers of cultural venues (point + name). Used to pin an
# event to its exact stage instead of the city centroid when the locationName
# matches. Source: Offene Daten Bonn (stadtplan.bonn.de).
_VENUE_GEOJSON_URLS = (
    "https://stadtplan.bonn.de/geojson?OD=4490",  # Schauspiel / Theater / Oper
    "https://stadtplan.bonn.de/geojson?OD=4489",  # Kleinkunst / Kabarett / Varieté
)

# Curated Bonn topic taxonomy → canonical public category. Unlike keyword
# hints, these finite source-owned topics are authoritative when unambiguous.
# Format-only values stay allowlisted but must remain classifier input.
_SOURCE_CATEGORY_MAP = {
    "Fest/Festival": "festival",
    "Musik/Konzert": "concert",
    "Kabarett": "stage",
    "Kabarett/Comedy": "stage",
    "Tanz": "stage",
    "Theater": "stage",
    "Theater/Oper": "stage",
    "Ausstellungen": "exhibition",
    "Ausstellung": "exhibition",
    "Tour": "outdoor",
    "Führung/Rundgang": "outdoor",
    "Lesung": "talk",
    "Vorträge/Lesungen/Diskussionen": "talk",
    "Vortrag/Diskussion": "talk",
    "Märkte/Messen": "market",
    "Markt/Messe": "market",
    "Film/Medien": "cinema",
    "Aktion/Workshop": "workshop",
    "Kurs": "workshop",
    "Treffen/Austausch": "activities",
    "Karneval": "festival",
    "Gedenkveranstaltung": "other",
    "Tag des offenen Denkmals": "festival",
    "Beethovenfest": "concert",
    "Weihnachtsmarkt": "market",
    "Wissenschaftsnacht-Vorträge": "talk",
}
_ALLOW = set(_SOURCE_CATEGORY_MAP) | {"Führungen/Rundgänge/Touren"}
_FREE_ACTIVITY_ALLOW = {
    "Aktion/Workshop", "Bonn-Information", "Familien/Kinder", "Ferienaktion",
    "Kinder (0 bis 5 Jahre)", "Kinder (5 bis 12 Jahre)", "Kultur", "Sport",
    "Tourismus",
}
_FREE_EVENT_SCORE_FLOOR = 0.45
_BLOCK = {
    "Sprechstunde", "Sitzung", "Sitzungstermine Ausschüsse", "Sitzungstermine Bezirksvertretung",
    "Informations-Veranstaltung", "Tagungen/Kongresse", "Stadtverwaltung",
    # Bonn singularised its labels; keep both spellings blocked so the rename
    # cannot quietly turn professional training into importable events.
    "Fortbildungen", "Fortbildung",
    "Beratung", "Spendenaktion", "Online-Veranstaltung", "Bürger*innenbeteiligung",
    "Next Stop Job", "Bürger*innensprechstunde OB Déus",
}
_KNOWN_SOURCE_CATEGORIES = (
    _ALLOW | _FREE_ACTIVITY_ALLOW | _BLOCK | {
        "Bonn", "Kostenlos",
        # Section/navigation labels can appear in teaser markup, but are not
        # event topics and must not make those cards importable.
        "Ausgehen. Erleben.", "Veranstaltungen. Kalender.", "Barrierefreie Stadt.",
        # Bonn's feed also mixes campaigns, audiences, districts, institutions,
        # accessibility facets and navigation labels into the category array.
        # They are known metadata dimensions, not canonical event formats; keep
        # them neutral instead of reporting the same false taxonomy drift on
        # every refresh.
        "100 Jahre Bad Godesberg", "30 Jahre UN-Stadt Bonn",
        "Aktiv gegen Einsamkeit", "Bad Godesberg", "Beethoven",
        "Beethoven-Orchester", "Beethovenhalle", "Beuel",
        "Bürgerschaftliches Engagement", "Demokratie", "Digitales/Bildung",
        "Erwachsene", "Europa", "Familien", "Ferienprogramm", "Frankreich",
        "Für Einzelgäste an festen Terminen",
        "Für Einzelgäste und Gruppen mit eigener Gästeführung",
        "Für Gruppen mit eigener Gästeführung", "Gesundheit", "Gleichstellung", "Hardtberg",
        "Haus der Natur", "Inklusionsthemen", "Inklusiv konzipiert",
        "Integration/Migration/Interkultur", "Internationales", "Jugendliche",
        "Junge Erwachsene", "Klima", "Kunstmuseum", "Nachhaltigkeit",
        "Beratungsstelle für Eltern, Kinder und Jugendliche", "SDG-Tage",
        "Seniorinnen und Senioren", "Sitzungstermine Rat",
        "Stadtbibliothek", "Startseite", "Volkshochschule", "Weihnachten",
        "Weitere Veranstaltungen", "Wissenschaft", "Wissenschaft / Wirtschaft",
        "barrierefreier Zugang", "inklusiv",
    }
)

_venue_points_cache = None
def _env_number(name: str, default: float) -> float:
    try:
        return max(float(os.environ.get(name, str(default))), 0)
    except (TypeError, ValueError):
        return default

def _reset_detail_context_cache() -> None:
    """Compatibility hook for isolated tests using the shared raw-page cache."""
    common._reset_detail_page_cache("bonn-detail")


def _loads_event_items(raw: str):
    """Parse Bonn's event payload, tolerating server log lines appended after JSON.

    The city endpoint has occasionally emitted a valid JSON array prefix followed
    by PHP/SiteKit log text when its server is unhealthy. Keep the source useful
    by trimming only that clearly marked trailing log tail.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"(?<=\})(?:\r?\n)?\[\d{4}-\d{2}-\d{2}T", raw)
        if not (raw.lstrip().startswith("[") and match):
            raise
        return json.loads(raw[:match.start()] + "]")


def _venue_points() -> dict:
    """Lazy {venue_name_lower: (lat, lon)} from the two Bonn GeoJSON layers."""
    global _venue_points_cache
    if _venue_points_cache is not None:
        return _venue_points_cache
    pts: dict = {}
    for url in _VENUE_GEOJSON_URLS:
        try:
            data = json.loads(common.fetch_url(
                url,
                timeout=15,
                accept="application/geo+json,application/json,*/*;q=0.8",
                sec_fetch_mode="cors",
                sec_fetch_dest="empty",
            ))
        except Exception as e:
            common.log_source_error("Bonn venue GeoJSON", e)
            continue
        for feat in data.get("features", []):
            name = ((feat.get("properties") or {}).get("name") or "").strip().lower()
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if name and len(coords) == 2:
                lon, lat = coords[0], coords[1]
                pts[name] = (lat, lon)
    _venue_points_cache = pts
    return pts


def _parse_dt(value: str):
    """Parse the feed's 'YYYY-MM-DD HH:MM:SS' (or bare date) into a datetime."""
    return common.parse_iso_date((value or "").strip())


def _concise_detail_description(value: str) -> str:
    """Clean detail enrichment, applying only an explicitly configured limit."""
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    max_chars = int(_env_number("NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS", 0))
    if not max_chars or len(cleaned) <= max_chars:
        return cleaned
    if max_chars == 1:
        return "…"

    room = max(max_chars - 1, 1)
    candidate = cleaned[:room].rstrip()
    sentence_ends = [
        match.end() for match in re.finditer(r'[.!?](?:["”’)]*)', candidate)
        if match.end() >= max_chars * 0.45
    ]
    if sentence_ends:
        shortened = candidate[:sentence_ends[-1]].rstrip()
    else:
        shortened = candidate.rsplit(" ", 1)[0].rstrip(" ,;:") or candidate
    return f"{shortened}…"


_DETAIL_LOGISTICS_PREFIX = re.compile(
    r"^(?:datum|uhrzeit|zeit|beginn|ende|lokation|veranstaltungsort|ort|künstler|"
    r"kuenstler|tickets?|preis|eintritt|anmeldung|kontakt)\s*:",
    re.I,
)


def _is_detail_logistics(text: str) -> bool:
    stripped = text.strip()
    return bool(
        _DETAIL_LOGISTICS_PREFIX.match(stripped)
        or re.match(r"^\d{1,2}:\d{2}\s+Uhr\b", stripped, re.I)
        or re.match(r"^\d{1,2}\.\d{1,2}\.20\d{2}$", stripped)
        or (len(stripped) <= 45 and re.match(r"^\d{1,2}\.\s+.*20\d{2}$", stripped))
    )


def _detail_rich_text(html: str) -> str:
    """The same body copy as the allowed HTML subset, headings and lists intact.

    ``_detail_paragraphs`` reduces each block to a line of text because the rest
    of the pipeline reasons over plain strings. The detail page can show more
    than that, so the raw fragments are sanitized a second time here — the
    logistics blocks that ``_join_detail_paragraphs`` drops are skipped so both
    forms describe the same event.
    """
    fragments = []
    intro = re.search(r'<div class="SP-ArticleHeader__intro[^"]*"[^>]*>(.*?)</div>', html, flags=re.S)
    if intro:
        fragments.append(intro.group(1))
    fragments.extend(re.findall(r'<div data-sp-table class="SP-Paragraph">(.*?)</div>', html, flags=re.S))

    kept = []
    for fragment in fragments:
        rendered = richtext.sanitize_rich_text(fragment)
        plain = richtext.to_plain_text(rendered)
        if plain and not _is_detail_logistics(plain):
            kept.append(rendered)
    return richtext.sanitize_rich_text(
        "".join(kept),
        int(_env_number("NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS", 0)) or None,
    )


def _detail_paragraphs(html: str) -> list[str]:
    parts = []
    intro = re.search(r'<div class="SP-ArticleHeader__intro[^"]*"[^>]*>(.*?)</div>', html, flags=re.S)
    if intro:
        text = common.clean_html(intro.group(1))
        if text:
            parts.append(text)

    for block in re.findall(r'<div data-sp-table class="SP-Paragraph">(.*?)</div>', html, flags=re.S):
        paragraphs = []
        for tag, raw in re.findall(r"<(p|li)\b[^>]*>(.*?)</\1>", block, re.S | re.I):
            text = common.clean_html(raw)
            if text:
                paragraphs.append(f"• {text}" if tag.casefold() == "li" else text)
        if paragraphs:
            parts.extend(paragraphs)
        else:
            text = common.clean_html(block)
            if text:
                parts.append(text)
    return parts


def _join_detail_paragraphs(parts: list[str]) -> list[str]:
    filtered = []
    index = 0
    while index < len(parts):
        text = re.sub(r"\s+", " ", parts[index]).strip()
        if not text or _is_detail_logistics(text):
            index += 1
            continue
        is_heading = (
            not text.startswith("• ")
            and len(text) <= 60
            and not re.search(r'\.["”’)]*$', text)
        )
        if is_heading and index + 1 < len(parts):
            following = re.sub(r"\s+", " ", parts[index + 1]).strip()
            if following and not _is_detail_logistics(following):
                separator = " " if re.search(r"[?:!]$", text) else ": "
                text = f"{text}{separator}{following}"
                index += 1
        filtered.append(text)
        index += 1
    return filtered


def _render_detail_paragraphs(paragraphs: list[str]) -> str:
    """Join the extracted paragraphs the way the detail page laid them out.

    These parts were already separated by the source's own ``<p>`` and ``<li>``
    tags; joining them with a space was what turned a structured page into one
    block of running text. Consecutive bullets stay a list rather than becoming
    a stack of one-line paragraphs.
    """
    rendered = ""
    for paragraph in paragraphs:
        if not rendered:
            rendered = paragraph
            continue
        both_bullets = paragraph.startswith("• ") and rendered.rsplit("\n", 1)[-1].startswith("• ")
        rendered += ("\n" if both_bullets else "\n\n") + paragraph
    return rendered


def _paragraph_aware_detail_description(parts: list[str]) -> str:
    paragraphs = _join_detail_paragraphs(parts)
    if not paragraphs:
        return ""
    max_chars = int(_env_number("NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS", 0))
    if not max_chars:
        return _render_detail_paragraphs(paragraphs)

    selected = []
    omitted = False
    for paragraph in paragraphs:
        candidate = _render_detail_paragraphs([*selected, paragraph])
        if len(candidate) <= max_chars:
            selected.append(paragraph)
            continue
        omitted = True
        if not selected:
            return _concise_detail_description(paragraph)
        break

    description = _render_detail_paragraphs(selected)
    if omitted and len(description) < max_chars:
        description = f"{description}…"
    return description


_ALWAYS_TICKETED_VENUE_PATTERN = re.compile(
    r"\bgop[-\s]*variet[ée](?:[-\s]*theater)?[-\s]*bonn\b",
    re.IGNORECASE,
)


def _detail_admission(html: str) -> str:
    """Read explicit visitor admission from Bonn's detail page."""
    for raw_label, raw_value in re.findall(
        r"<tr\b[^>]*>\s*<th\b[^>]*>(.*?)</th>\s*<td\b[^>]*>(.*?)</td>\s*</tr>",
        html or "",
        re.I | re.S,
    ):
        label = common.clean_html(raw_label)
        if label.casefold() not in {"einlass", "eintritt", "kosten", "preis"}:
            continue
        value = common.clean_html(raw_value)
        if price := common.infer_free_admission_price(label, value):
            return price

    admission_section = next((
        section for section in re.findall(
            r"<section\b[^>]*>.*?</section>",
            html or "",
            re.IGNORECASE | re.DOTALL,
        )
        if re.search(r"\bid=[\"']eintritt[\"']", section, re.IGNORECASE)
    ), "")
    if admission_section:
        for raw_paragraph in re.findall(
            r"<p\b[^>]*>(.*?)</p>",
            admission_section,
            re.IGNORECASE | re.DOTALL,
        ):
            paragraph = common.clean_html(raw_paragraph)
            if price := common.infer_free_admission_price("Eintritt", paragraph):
                return price
        section_text = common.clean_html(admission_section)
        if price := common.infer_free_admission_price("Eintritt", section_text):
            return price
        amounts = re.findall(
            r"(?<!\d)(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\b|euro\b)",
            section_text,
            re.IGNORECASE,
        )
        if amounts:
            values = [float(amount.replace(",", ".")) for amount in amounts]
            return "kostenpflichtig" if any(value > 0 for value in values) else "kostenlos"
    return ""


def _event_admission(price: str, venue: str, detail_context: dict) -> tuple[str, str]:
    """Resolve stale Bonn category tags against stronger event/venue evidence."""
    if _ALWAYS_TICKETED_VENUE_PATTERN.search(common.clean_html(venue or "")):
        return "kostenpflichtig", "explicit"
    detail_price = detail_context.get("price", "")
    if detail_price:
        return detail_price, detail_context.get("admission_basis", "")
    return price, ""


def _parse_detail_context(html: str) -> dict:
    """Extract description and structured location facts from a Bonn detail page."""
    context = {
        "description": "",
        "description_html": "",
        "venue": "",
        "venue_address": "",
        "venue_latitude": None,
        "venue_longitude": None,
        "city": "",
        "start_time": "",
        "end_time": "",
    }

    schedule_match = re.search(
        r'''<section[^>]+class=["'][^"']*(?:EventInformation__date|SP-EventInformation__scheduling)[^"']*["'][^>]*>(.*?)</section>''',
        html or "",
        re.I | re.S,
    )
    if schedule_match:
        clocks = re.findall(
            r'<span[^>]+class="[^"]*SP-Scheduling__time[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
            schedule_match.group(1),
            re.I | re.S,
        )
        normalized_clocks = [
            match.group(0)
            for value in clocks
            if (match := re.search(r"\b\d{1,2}:\d{2}\b", common.clean_html(value)))
        ]
        if normalized_clocks:
            context["start_time"] = normalized_clocks[0]
        if len(normalized_clocks) > 1:
            context["end_time"] = normalized_clocks[1]

    # Some municipal records are syndicated copies and name the organizer's
    # first-party detail page explicitly.  Prefer that visitor-facing source
    # while retaining Bonn.de in source_links as discovery provenance.  Keep
    # this deliberately narrow to the Kunstmuseum exhibition route: generic
    # external links on event pages also include maps, ticket shops and sponsors.
    primary_match = re.search(
        r'href="(https://www\.kunstmuseum-bonn\.de/de/ausstellungen/[^"?#]+/)"',
        html or "",
        re.I,
    )
    if primary_match:
        context["primary_url"] = primary_match.group(1)

    seen = set()
    description_parts = []
    for text in _detail_paragraphs(html):
        key = text.lower()
        if key and key not in seen:
            seen.add(key)
            description_parts.append(text)
    context["description"] = _paragraph_aware_detail_description(description_parts)
    context["description_html"] = _detail_rich_text(html)
    if price := _detail_admission(html):
        context["price"] = price
        context["admission_basis"] = "explicit"

    for raw_json in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.S):
        try:
            data = json.loads(unescape(raw_json).strip())
        except Exception:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        nodes = list(graph) if isinstance(graph, list) else []
        if isinstance(data, dict):
            nodes.append(data)
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") != "Event":
                continue
            schedules = node.get("eventSchedule") or []
            if isinstance(schedules, dict):
                schedules = [schedules]
            for schedule in schedules:
                if not isinstance(schedule, dict):
                    continue
                if not context["start_time"]:
                    context["start_time"] = str(schedule.get("startTime") or "").strip()
                if not context["end_time"]:
                    context["end_time"] = str(schedule.get("endTime") or "").strip()
                if context["start_time"] and context["end_time"]:
                    break
            locations = node.get("location") or []
            if isinstance(locations, dict):
                locations = [locations]
            if not locations:
                continue
            location = locations[0] or {}
            if not isinstance(location, dict):
                continue
            context["venue"] = (location.get("name") or "").strip()
            address = location.get("address") or {}
            if isinstance(address, dict):
                street = str(address.get("streetAddress") or "").strip()
                postal_code = str(address.get("postalCode") or "").strip()
                context["city"] = str(address.get("addressLocality") or "").strip()
                locality = " ".join(part for part in (postal_code, context["city"]) if part)
                context["venue_address"] = ", ".join(part for part in (street, locality) if part)
            elif isinstance(address, str):
                context["venue_address"] = common.clean_html(address)
            geo = location.get("geo") or {}
            if isinstance(geo, dict):
                try:
                    latitude = float(geo.get("latitude"))
                    longitude = float(geo.get("longitude"))
                except (TypeError, ValueError):
                    pass
                else:
                    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                        context["venue_latitude"] = latitude
                        context["venue_longitude"] = longitude
            return context

    return context


def _apply_detail_time(event: dict, context: dict) -> dict:
    """Fill a listing occurrence's missing end time from its detail page."""
    start_time = str(context.get("start_time") or "").strip()
    end_time = str(context.get("end_time") or "").strip()
    if not (re.fullmatch(r"\d{1,2}:\d{2}", start_time) and re.fullmatch(r"\d{1,2}:\d{2}", end_time)):
        return event
    start_time = ":".join((start_time.split(":", 1)[0].zfill(2), start_time.split(":", 1)[1]))
    end_time = ":".join((end_time.split(":", 1)[0].zfill(2), end_time.split(":", 1)[1]))
    listed_start = str(event.get("time") or "").split("–", 1)[0]
    if listed_start not in ("", start_time):
        return event
    occurrence = common.parse_iso_date(str(event.get("start_date") or event.get("date") or ""))
    if not occurrence:
        return event
    start_hour, start_minute = map(int, start_time.split(":"))
    end_hour, end_minute = map(int, end_time.split(":"))
    start = occurrence.replace(hour=start_hour, minute=start_minute, tzinfo=common.LOCAL_TIMEZONE)
    end = occurrence.replace(hour=end_hour, minute=end_minute, tzinfo=common.LOCAL_TIMEZONE)
    if end <= start:
        end += timedelta(days=1)
    event["time"] = f"{start_time}–{end_time}"
    event["start_at"] = start.isoformat(timespec="minutes")
    event["end_at"] = end.isoformat(timespec="minutes")
    event["end_date"] = end.strftime("%Y-%m-%d")
    event["all_day"] = False
    event["ongoing"] = False
    return event


def _fetch_detail_context(link: str, timeout: float = 15) -> dict:
    if not link or "bonn.de/veranstaltungskalender/" not in link:
        return {}
    try:
        html = common.fetch_detail_url(
            link,
            cache_namespace="bonn-detail",
            cache_failures=True,
            timeout=timeout,
            retry_attempts=1,
            accept="text/html,*/*;q=0.8",
            sec_fetch_mode="navigate",
            sec_fetch_dest="document",
        )
        return _parse_detail_context(html)
    except Exception as e:
        common.log_source_error("Bonn.de detail", e)
        return {}


def _apply_detail_location(event: dict, context: dict) -> dict:
    """Fill missing canonical location fields from Bonn.de's Event JSON-LD."""
    for field in (
        "venue_address",
        "venue_latitude",
        "venue_longitude",
    ):
        if event.get(field) in (None, "") and context.get(field) not in (None, ""):
            event[field] = context[field]
    return event


def _apply_detail_source_link(event: dict, context: dict) -> dict:
    """Promote a verified first-party exhibition URL without losing provenance."""
    primary_url = str(context.get("primary_url") or "").strip()
    municipal_url = str(event.get("link") or "").strip()
    if not primary_url:
        return event
    event["link"] = primary_url
    event["link_kind"] = "detail"
    event["source_links"] = list(dict.fromkeys(filter(None, (
        *(event.get("source_links") or []), municipal_url, primary_url,
    ))))
    return event


def _clean_free_title_prefix(title: str) -> str:
    """Remove Bonn.de editorial free-entry prefixes from display titles."""
    return re.sub(r"^\s*(?:kostenloser\s+eintritt|eintritt\s+frei)\s*:\s*", "", title or "", flags=re.I).strip()


def _unknown_source_categories(tags: set[str]) -> set[str]:
    return tags - _KNOWN_SOURCE_CATEGORIES


def _warn_unknown_source_categories(source: str, categories: set[str]) -> None:
    if categories:
        common.log_source_error(
            f"{source} category taxonomy",
            ValueError("unknown Bonn source categories: " + ", ".join(sorted(categories))),
        )


def _apply_source_category_mapping(ev: dict, tags: set[str]) -> dict:
    mapped_tags = sorted(tag for tag in tags if tag in _SOURCE_CATEGORY_MAP)
    mapped_keys = {_SOURCE_CATEGORY_MAP[tag] for tag in mapped_tags}
    if len(mapped_keys) != 1:
        # Conflicting or absent source categories remain keyword-classifier input.
        return ev
    key = mapped_keys.pop()
    category = category_taxonomy.CATEGORY_BY_KEY[key]
    return {
        **ev,
        "category_key": key,
        "category_label": category["label"],
        "category_confidence": 1.0,
        "category_reason": "bonn-source-category:" + ", ".join(mapped_tags),
    }


def _apply_free_category_override(ev: dict, tags: set) -> dict:
    """Respect strong Bonn tags for free records admitted through free_allow."""
    if "Sport" in tags:
        return {
            **ev,
            "category_key": "sports",
            "category_label": "Sport & Bewegung",
            "category_confidence": max(ev.get("category_confidence", 0), 0.86),
            "category_reason": f"bonn-free-tag:Sport; {ev.get('category_reason', '')}".strip(),
        }
    if tags & {"Familien/Kinder", "Ferienaktion", "Kinder (0 bis 5 Jahre)", "Kinder (5 bis 12 Jahre)"}:
        return {
            **ev,
            "category_key": "kids",
            "category_label": "Familie & Kinder",
            "category_confidence": max(ev.get("category_confidence", 0), 0.86),
            "category_reason": f"bonn-free-tag:Familien/Kinder; {ev.get('category_reason', '')}".strip(),
        }
    return ev


def fetch_events() -> list:
    """Official Bonn calendar → union of every available Bonn event feed.

    The server-rendered listings remain the coverage baseline because Bonn's
    JSON and RSS endpoints can be incomplete.  They are enrichment sources,
    though, not emergency-only fallbacks: structured records regularly carry
    end times and other facts that the listing cards omit.  Detail-page fetches
    still use the shared persistent TTL cache.
    """
    source = "Bonn.de Events"
    free_events = _fetch_free_calendar_events(source, enrich_details=False)
    calendar_events = _fetch_calendar_listing_events(source, enrich_details=False)
    events = _merge_fallback_events(free_events, calendar_events)
    events = _merge_fallback_events(
        events, _fetch_rss_events(source, enrich_details=False),
    )
    events = _merge_fallback_events(
        events,
        fetch_events_json(source, include_fallbacks=False, enrich_details=False),
    )
    return _drop_redundant_dated_title_variants(_enrich_listing_details(events))


def fetch_events_json(
    source: str = "Bonn.de Events",
    *,
    include_fallbacks: bool = True,
    enrich_details: bool = True,
) -> list:
    """Legacy JSON fallback → dated, activity-only, venue-pinned events."""
    try:
        items = _loads_event_items(common.fetch_url(
            _EVENTS_JSON_URL,
            timeout=25,
            accept="application/json,*/*;q=0.8",
            sec_fetch_mode="cors",
            sec_fetch_dest="empty",
        ))
    except Exception as e:
        if include_fallbacks:
            fallback = _fetch_rss_events(source)
            if fallback:
                return fallback
        common.log_source_error(source, e)
        return []
    if not isinstance(items, list):
        items = items.get("events", []) if isinstance(items, dict) else []

    points = _venue_points()
    events = []
    unknown_categories = set()
    for item in items:
        title = (item.get("title") or "").strip()
        if not title:
            continue

        tags = set(item.get("category") or [])
        allow = tags & _ALLOW
        unknown = _unknown_source_categories(tags)
        unknown_categories.update(unknown)
        price = common.infer_free_admission_price(
            item.get("title", ""), item.get("description", ""),
            "kostenlos" if "Kostenlos" in tags else "",
        )
        free_allow = (tags & _FREE_ACTIVITY_ALLOW) if price else set()
        if (not allow and not free_allow) or (tags & _BLOCK):
            continue

        start_dt = _parse_dt(item.get("startDate", ""))
        end_dt = _parse_dt(item.get("endDate", "")) or start_dt
        if not start_dt:
            continue  # no date → not a short-term plannable activity

        link = (item.get("link") or "").strip()
        description = (item.get("description") or "").strip()
        # Detail copy is display enrichment.  Keep the source record's original
        # classifier input so wording on a recurring meeting page cannot make a
        # valid structured occurrence disappear before it enriches the listing.
        classification_description = description
        venue = (item.get("locationName") or "").strip()
        identity_venue = venue
        detail_context = {}
        location_address = (item.get("locationAddress") or "").strip()
        if (
            enrich_details
            and link
            and common.window_contains(start_dt, end_dt)
            and (not description or not venue or not location_address)
        ):
            detail_context = _fetch_detail_context(link)
            description = description or detail_context.get("description", "")
            venue = venue or detail_context.get("venue", "")
            location_address = location_address or detail_context.get("venue_address", "")
            price = detail_context.get("price") or price
        parts = [p.strip() for p in location_address.split(",") if p.strip()]
        town = re.sub(r"^\d{4,5}\s*", "", parts[-1]).strip() if parts else detail_context.get("city", "")
        city = common.refine_city_from_text(
            town or "Bonn", " ".join((title, venue, description))
        )
        price, resolved_admission_basis = _event_admission(price, venue, detail_context)

        # Only the time string and the venue-coordinate pin are Bonn-specific;
        # make_event owns the window/radius/date/dict/junk machinery.
        time_text = ""
        if item.get("hasStartTime") and (start_dt.hour or start_dt.minute):
            time_text = f"{start_dt:%H:%M}"
            if item.get("hasEndTime") and end_dt and (end_dt.hour or end_dt.minute):
                time_text += f"–{end_dt:%H:%M}"

        category_tags = allow or free_allow
        ev = common.make_event(
            title, start_dt, end_dt, venue, city, classification_description, link,
            source, ", ".join(sorted(category_tags)), time_text=time_text,
            coords=points.get(venue.lower()))
        if ev:
            # Detail-page location data is enrichment, not a new occurrence.
            # Lock even an originally empty listing venue so a newly discovered
            # Place does not move an already published event URL.
            ev["identity_venue"] = identity_venue
            ev["identity_venue_locked"] = True
            if location_address and not ev.get("venue_address"):
                ev["venue_address"] = location_address
            ev = _apply_detail_location(ev, detail_context)
            ev = _apply_detail_source_link(ev, detail_context)
            ev["description"] = common.concise_description(description, max_chars=0)
            ev["description_html"] = detail_context.get("description_html") or richtext.from_plain_text(ev["description"])
            ev = _apply_source_category_mapping(ev, tags)
            if free_allow and not allow:
                ev = _apply_free_category_override(ev, tags)
            if price:
                ev["price"] = price
                if resolved_admission_basis:
                    ev["admission_basis"] = resolved_admission_basis
                if free_allow:
                    ev["score"] = max(ev.get("score", 0), _FREE_EVENT_SCORE_FLOOR)
            events.append(ev)
    _warn_unknown_source_categories(source, unknown_categories)
    if include_fallbacks and len(events) < 20:
        events = _merge_fallback_events(events, _fetch_rss_events(source))
        events = _merge_fallback_events(events, _fetch_free_calendar_events(source))
        events = _merge_fallback_events(events, _fetch_calendar_listing_events(source))
    return events


_HTML_URL = "https://www.bonn.de/bonn-erleben/ausgehen-und-erleben/veranstaltungskalender.php"
_SPORTS_URL = "https://www.bonn.de/bonn-erleben/aktiv-und-unterwegs/sportveranstaltungen.php"
_RSS_URL = (_HTML_URL + "?sp%3Aout=rss&sp%3Acmp=search-1-0-searchResult&action=submit")
# Annual press release. The slug embeds the year; we build it dynamically so the
# source keeps working in future years with no code change (no dates hardcoded).
_PRESS_MONTH_PATHS = (
    "dezember", "november", "januar", "oktober", "september", "august",
    "juli", "juni", "mai", "april", "maerz", "februar",
)

def _active_reviewed_map(group: str) -> dict[tuple[str, ...], object]:
    return {
        tuple(str(value) for value in entry["match"]): entry["value"]
        for entry in reviewed_corrections.active_entries(group, common.runtime_window().start)
    }


def _press_urls(year: int) -> tuple[str, ...]:
    slug = f"abwechslungsreiches-veranstaltungsjahr-{year}-in-bonn.php"
    return tuple(
        f"https://www.bonn.de/pressemitteilungen/{month}/{slug}"
        for month in _PRESS_MONTH_PATHS
    )


def _calendar_search_url(page: int = 1, *, free_only: bool = True) -> str:
    params = [
        ("sp:dateFrom[]", common.runtime_window().start.strftime("%Y-%m-%d")),
        ("sp:dateTo[]", common.runtime_window().end.strftime("%Y-%m-%d")),
        ("action", "submit"),
    ]
    if free_only:
        params[0:0] = [
            ("sp:categories[1530][]", "326135"),  # Zielgruppe → Kostenlos
            ("sp:categories[1530][]", "__last__"),
        ]
    if page > 1:
        params.append(("sp:page[search-1.form][0]", str(page)))
    return _HTML_URL + "?" + common.urllib.parse.urlencode(params)


def _clean_event_href(href: str) -> str:
    """Remove Bonn's transient signature while preserving functional queries."""
    parsed = common.urllib.parse.urlsplit(href)
    query = common.urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [
        (key, value)
        for key, value in query
        if not (key == "p" and value.casefold().startswith("sig:"))
    ]
    return common.urllib.parse.urlunsplit(
        (*parsed[:3], common.urllib.parse.urlencode(query), parsed.fragment)
    )


def _pagination_max(html: str) -> int:
    return rc.pagination_max(html)


def _split_tags(value: str) -> set:
    return {part.strip() for part in re.split(r"\s*(?:,|\|)\s*", value or "") if part.strip()}


def _is_sparse_listing_description(description: str, title: str) -> bool:
    normalized = re.sub(r"[^\wäöüß]+", " ", description or "", flags=re.I).strip().lower()
    normalized_title = re.sub(r"[^\wäöüß]+", " ", title or "", flags=re.I).strip().lower()
    normalized_display_title = re.sub(
        r"[^\wäöüß]+", " ", _clean_free_title_prefix(title), flags=re.I
    ).strip().lower()
    if not normalized or normalized in {normalized_title, normalized_display_title}:
        return True
    return bool(re.fullmatch(
        r"(?:zur )?anmeldung(?: erforderlich| erbeten)?|weitere informationen|mehr erfahren",
        normalized,
    ))


def _enrich_listing_details(events: list[dict]) -> list[dict]:
    """Enrich final in-window listing rows within one separate detail budget."""
    batch_timeout = float(os.environ.get("NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS", "45"))
    deadline = time.monotonic() + max(batch_timeout, 0.0)
    enriched = [dict(event) for event in events]
    ordered = sorted(
        range(len(enriched)),
        key=lambda index: not (
            enriched[index].get("description_source") == "generated"
            or _is_sparse_listing_description(
                str(enriched[index].get("description") or ""),
                str(enriched[index].get("title") or ""),
            )
        ),
    )
    contexts: dict[str, dict] = {}
    failed_links: set[str] = set()
    for index in ordered:
        event = enriched[index]
        weak_description = (
            event.get("description_source") == "generated"
            or _is_sparse_listing_description(
                str(event.get("description") or ""), str(event.get("title") or ""),
            )
        )
        needs_detail = (
            weak_description
            or not event.get("venue")
            or not event.get("venue_address")
            or event.get("category_key") == "other"
        )
        link = str(event.get("link") or "").strip()
        if not needs_detail or not link or not common.event_in_window(event):
            continue
        # This source adapter owns the detail URL for this row. Mark the
        # attempt even when its optional batch budget is exhausted or the
        # request fails, so the generic enrichment pass does not fetch the
        # same URL again under a second cache namespace.
        event["_detail_page_enriched"] = True
        remaining = deadline - time.monotonic()
        if link not in contexts and link not in failed_links:
            if remaining < 3.0:
                continue
            request_timeout = 15.0 if remaining >= 30.0 else max(1.0, remaining / 3.0)
            context = _fetch_detail_context(link, timeout=request_timeout)
            if context:
                contexts[link] = context
            else:
                failed_links.add(link)
        context = contexts.get(link, {})
        if not context:
            continue
        if weak_description and context.get("description"):
            event["description"] = common.concise_description(context["description"], max_chars=0)
            event["description_source"] = "scraped"
        if weak_description and context.get("description_html"):
            event["description_html"] = context["description_html"]
        if not event.get("venue") and context.get("venue"):
            event["venue"] = context["venue"]
        event["city"] = common.refine_city_from_text(
            str(context.get("city") or event.get("city") or "Bonn"),
            " ".join(str(event.get(field) or "") for field in ("title", "venue", "description")),
        )
        event = _apply_detail_time(event, context)
        event = _apply_detail_location(event, context)
        event = _apply_detail_source_link(event, context)
        price, admission_basis = _event_admission(
            str(event.get("price") or ""), str(event.get("venue") or ""), context,
        )
        if price:
            event["price"] = price
            if admission_basis:
                event["admission_basis"] = admission_basis
        enriched[index] = event
    return enriched


def _listing_events_from_html(
    html: str, source: str, *, free_only: bool = False, fetch_details: bool = True,
) -> list:
    events, seen = [], set()
    unknown_categories = set()
    for body in rc.class_tag_blocks(html, "article", "SP-Teaser"):
        href = rc.attribute_from_class_tag(body, "a", "SP-Teaser__inner", "href")
        title_m = re.search(r'<h1[^>]+class="[^"]*SP-Teaser__headline[^"]*"[^>]*>(.*?)</h1>', body, re.S | re.I)
        cat_m = re.search(r'<span[^>]+class="[^"]*SP-Kicker__text[^"]*"[^>]*>(.*?)</span>', body, re.S | re.I)
        if not (href and title_m):
            continue
        href = _clean_event_href(href)
        if "/veranstaltungskalender/veranstaltungen/" not in href:
            continue

        raw_title = common.clean_html(title_m.group(1))
        title = _clean_free_title_prefix(raw_title)
        category = common.clean_html(cat_m.group(1) if cat_m else "")
        tags = _split_tags(category)
        allow = tags & _ALLOW
        free_allow = tags & _FREE_ACTIVITY_ALLOW
        unknown = _unknown_source_categories(tags)
        unknown_categories.update(unknown)
        if (not allow and not free_allow) or (tags & _BLOCK):
            continue

        link = common.urllib.parse.urljoin("https://www.bonn.de", href)
        abstract_m = re.search(r'<div[^>]+class="[^"]*SP-Teaser__abstract[^"]*"[^>]*>(.*?)</div>', body, re.S | re.I)
        listing_description = common.clean_html(abstract_m.group(1) if abstract_m else "")
        date_matches = re.findall(
            r'<span>\s*<span[^>]+class="[^"]*SP-Scheduling__date[^"]*"[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*</span>'
            r'(?:\s*<span[^>]+class="[^"]*SP-Scheduling__time[^"]*"[^>]*>\s*([^<]*?)\s*</span>)?\s*</span>',
            body,
            re.S | re.I,
        )
        has_in_window_occurrence = any(
            common.window_contains(common.parse_date(date_text))
            for date_text, _ in date_matches
        )
        description = listing_description
        classification_description = listing_description
        venue, city = "", "Bonn"
        detail_context = {}
        if has_in_window_occurrence and fetch_details:
            detail_context = _fetch_detail_context(link)
            venue = detail_context.get("venue", "")
            city = detail_context.get("city", "") or city
            if _is_sparse_listing_description(listing_description, raw_title):
                description = detail_context.get("description", "") or raw_title
                # Rich article copy is display enrichment. Keep the official teaser
                # title/categories as the ranking input so words in a long detail
                # page cannot make a valid listed event disappear below the score
                # floor (for example Nachtwache or Das Stadtspiel).
                classification_description = raw_title
        city = common.refine_city_from_text(
            city, " ".join((title, venue, listing_description, description))
        )
        for date_text, time_raw in date_matches:
            start = common.parse_date(date_text)
            if not start:
                continue
            time_text = common.clean_html(time_raw)
            time_match = re.search(r"(\d{1,2}):(\d{2})", time_text)
            if time_match and (int(time_match.group(1)), int(time_match.group(2))) != (0, 0):
                start = start.replace(hour=int(time_match.group(1)), minute=int(time_match.group(2)))
                time_text = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
            else:
                time_text = ""

            key = (title.lower(), start.strftime("%Y-%m-%d"), link)
            if key in seen:
                continue
            seen.add(key)
            ev = common.make_event(
                title, start, start, venue, city, classification_description, link,
                source, ", ".join(sorted(tags | ({"Kostenlos"} if free_only else set()))), trust=0.86, time_text=time_text,
            )
            if ev:
                ev["identity_venue"] = ""
                ev["identity_venue_locked"] = True
                ev = _apply_detail_time(ev, detail_context)
                ev = _apply_detail_location(ev, detail_context)
                ev = _apply_detail_source_link(ev, detail_context)
                ev = _apply_source_category_mapping(ev, tags)
                if description != classification_description:
                    ev["description"] = common.concise_description(description, max_chars=0)
                ev["description_html"] = detail_context.get("description_html") or richtext.from_plain_text(ev["description"])
                fallback_price = common.infer_free_admission_price(
                    raw_title, description,
                    "kostenlos" if free_only or "Kostenlos" in tags else "",
                )
                price, resolved_admission_basis = _event_admission(
                    fallback_price, venue, detail_context,
                )
                if price:
                    ev["price"] = price
                    if resolved_admission_basis:
                        ev["admission_basis"] = resolved_admission_basis
                if free_allow and not allow:
                    ev = _apply_free_category_override(ev, tags)
                    if price:
                        ev["score"] = max(ev.get("score", 0), _FREE_EVENT_SCORE_FLOOR)
                events.append(ev)
    _warn_unknown_source_categories(source, unknown_categories)
    return events


def _free_listing_events_from_html(html: str, source: str) -> list:
    return _listing_events_from_html(html, source, free_only=True)


def _calendar_listing_events_from_html(html: str, source: str) -> list:
    return _listing_events_from_html(html, source, free_only=False)


def _fetch_calendar_listing_events(
    source: str = "Bonn.de Events", *, enrich_details: bool = True,
) -> list:
    """Crawl Bonn's server-rendered calendar result pages.

    Bonn's structured JSON and RSS feeds sometimes omit valid municipal events
    even while the normal calendar search lists them. Crawling the dated listing
    recovers those non-``extern`` entries (for example Musikschule concerts)
    without hard-coding individual event URLs.
    """
    try:
        first = common.fetch_url(_calendar_search_url(free_only=False), timeout=25)
    except Exception as e:
        common.log_source_error(f"{source} calendar listing fallback", e)
        return []

    events = _listing_events_from_html(first, source, fetch_details=False)
    max_page = min(_pagination_max(first), int(os.environ.get("NRW_EVENTS_BONN_CALENDAR_MAX_PAGES", "30")))
    for page in range(2, max_page + 1):
        try:
            events = _merge_fallback_events(
                events,
                _listing_events_from_html(
                    common.fetch_url(_calendar_search_url(page, free_only=False), timeout=25),
                    source,
                    fetch_details=False,
                ),
            )
        except Exception as e:  # noqa: PERF203 - pagination failures are isolated per page
            common.log_source_error(f"{source} calendar listing fallback page {page}", e)
            continue
    return _enrich_listing_details(events) if enrich_details else events


def _fetch_free_calendar_events(
    source: str = "Bonn.de Events", *, enrich_details: bool = True,
) -> list:
    """Fallback: crawl Bonn's free-category listing when the JSON feed is broken.

    The public JSON endpoint occasionally truncates before current-day entries.
    The server-rendered calendar still exposes the "Kostenlos" category via
    paginated SP-Teaser cards, which is enough to recover free-entry events.
    """
    try:
        first = common.fetch_url(_calendar_search_url(free_only=True), timeout=25)
    except Exception as e:
        common.log_source_error(f"{source} free calendar fallback", e)
        return []

    events = _listing_events_from_html(first, source, free_only=True, fetch_details=False)
    max_page = min(_pagination_max(first), 20)
    for page in range(2, max_page + 1):
        try:
            events = _merge_fallback_events(
                events,
                _listing_events_from_html(
                    common.fetch_url(_calendar_search_url(page, free_only=True), timeout=25),
                    source,
                    free_only=True,
                    fetch_details=False,
                ),
            )
        except Exception as e:  # noqa: PERF203 - pagination failures are isolated per page
            common.log_source_error(f"{source} free calendar fallback page {page}", e)
            continue
    return _enrich_listing_details(events) if enrich_details else events


def _parse_sport_time(text: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def events_from_sport_teasers(html: str) -> list:
    """Parse the Bonn.de Sportveranstaltungen teaser list into dated events."""
    source = "Bonn.de Sports"
    events, seen = [], set()
    for body in rc.class_tag_blocks(html, "article", "SP-Teaser"):
        href = rc.attribute_from_class_tag(body, "a", "SP-Teaser__inner", "href")
        title_m = re.search(r'<h1[^>]+class="[^"]*SP-Teaser__headline[^"]*"[^>]*>(.*?)</h1>', body, re.S | re.I)
        cat_m = re.search(r'<span[^>]+class="[^"]*SP-Kicker__text[^"]*"[^>]*>(.*?)</span>', body, re.S | re.I)
        if not (href and title_m):
            continue
        title = common.clean_html(title_m.group(1))
        category = common.clean_html(cat_m.group(1) if cat_m else "Sport") or "Sport"
        link = common.urllib.parse.urljoin(
            "https://www.bonn.de", _clean_event_href(href),
        )
        for date_text, time_raw in re.findall(
            r'<span[^>]+class="[^"]*SP-Scheduling__date[^"]*"[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*</span>'
            r'(?:\s*<span[^>]+class="[^"]*SP-Scheduling__time[^"]*"[^>]*>\s*([^<]*?)\s*</span>)?',
            body, re.S | re.I,
        ):
            start = common.parse_date(date_text)
            time_text = _parse_sport_time(common.clean_html(time_raw))
            if start and time_text:
                hour, minute = map(int, time_text.split(":"))
                start = start.replace(hour=hour, minute=minute)
            key = (title.lower(), start.strftime("%Y-%m-%d") if start else date_text, time_text)
            if key in seen:
                continue
            seen.add(key)
            ev = common.make_event(
                title, start, start, "", "Bonn",
                common.factual_event_description(
                    title, date_value=start, time_text=time_text, city="Bonn"),
                link, source, category, trust=0.8, time_text=time_text,
            )
            if ev:
                events.append(ev)
    return events


def _apply_reviewed_sport_occurrence_corrections(events: list[dict]) -> list[dict]:
    """Collapse BonnFest teaser rows into the official 25–27 September range."""
    corrected: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    primary_urls = _active_reviewed_map("bonn_press_primary_urls")
    overrides = _active_reviewed_map("bonn_press_overrides")
    bonnfest_key = next(
        (key for key in primary_urls if key[0].casefold().startswith("bonnfest")),
        None,
    )
    bonnfest_start = datetime.fromisoformat(bonnfest_key[1]) if bonnfest_key else None
    bonnfest_end = datetime.fromisoformat(bonnfest_key[2]) if bonnfest_key else None
    for event in events:
        candidate = dict(event)
        event_date = common.parse_iso_date(str(candidate.get("start_date") or ""))
        detail_path = common.urllib.parse.urlparse(str(candidate.get("link") or "")).path
        if (
            bonnfest_key is not None
            and common.clean_html(str(candidate.get("title") or "")) == bonnfest_key[0]
            and event_date
            and bonnfest_start
            and bonnfest_end
            and bonnfest_start.date() <= event_date.date() <= bonnfest_end.date()
            and detail_path == common.urllib.parse.urlparse(
                str(primary_urls[bonnfest_key])
            ).path
        ):
            candidate.update({
                "date": bonnfest_key[1],
                "start_date": bonnfest_key[1],
                "end_date": bonnfest_key[2],
                "time": "",
                "start_at": "",
                "end_at": "",
                "all_day": True,
                "description_html": "",
                "description_source": "generated",
            })
            candidate.update(overrides.get(bonnfest_key, {}))
        key = (
            common.clean_html(str(candidate.get("title") or "")).casefold(),
            str(candidate.get("start_date") or ""),
            str(candidate.get("end_date") or ""),
            str(candidate.get("link") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        corrected.append(candidate)
    return corrected


def _enrich_sport_details(events: list[dict], detail_fetcher) -> list[dict]:
    """Recover official Bonn.de Place data omitted from sport teaser cards."""
    enriched = []
    for event in events:
        event_date = common.parse_iso_date(str(event.get("start_date") or ""))
        if (
            event.get("venue")
            or not event.get("link")
            or not common.window_contains(event_date)
        ):
            enriched.append(event)
            continue
        locked = dict(event)
        locked["identity_venue"] = ""
        locked["identity_venue_locked"] = True
        try:
            document = detail_fetcher(str(event["link"]))
            context = detail_enrichment.extract_detail_context(document, locked)
            locked = detail_enrichment.apply_detail_context(locked, context)
        except Exception as exc:
            common.log_source_error("Bonn.de Sports detail", exc)
        enriched.append(locked)
    return enriched


def fetch_sports() -> list:
    source = "Bonn.de Sports"
    try:
        events = events_from_sport_teasers(common.fetch_url(_SPORTS_URL, timeout=20))
        return _apply_reviewed_sport_occurrence_corrections(
            _enrich_sport_details(
                events,
                lambda url: common.fetch_detail_url(
                    url,
                    cache_namespace="bonn-sports-detail",
                    timeout=15,
                    retry_attempts=1,
                ),
            ),
        )
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _fetch_rss_events(
    source: str = "Bonn.de RSS", *, enrich_details: bool = True,
) -> list:
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(common.fetch_url(
            _RSS_URL,
            accept="application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
            sec_fetch_mode="no-cors",
            sec_fetch_dest="empty",
        ))
        events = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            pub_date = (item.findtext("pubDate") or "").strip()
            desc = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc_text = unescape(re.sub(r"<[^>]+>", "", desc)) if desc else ""
            event_date = common.parse_date(pub_date)
            ev = common.make_event(
                unescape(title),
                event_date,
                event_date,
                "",
                "Bonn",
                desc_text,
                link,
                source,
                "official calendar rss bonn",
                trust=0.76,
            )
            if (
                enrich_details
                and ev
                and common.window_contains(event_date)
                and ev.get("category_key") == "other"
            ):
                detail_context = _fetch_detail_context(link)
                detail_description = detail_context.get("description") or ""
                if detail_description:
                    enriched = common.make_event(
                        unescape(title),
                        event_date,
                        event_date,
                        detail_context.get("venue", ""),
                        detail_context.get("city") or "Bonn",
                        detail_description,
                        link,
                        source,
                        "official calendar rss bonn",
                        trust=0.76,
                    )
                    if enriched:
                        enriched["description"] = common.concise_description(
                            detail_description, max_chars=0
                        )
                        ev = enriched
            if ev:
                price = common.infer_free_admission_price(title, desc)
                if price:
                    ev["price"] = price
                events.append(ev)
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []


_MERGE_IDENTITY_FIELDS = {
    "event_id", "identity_venue", "identity_venue_locked", "title", "link",
    "date", "start_date", "source", "source_id", "source_role", "score",
    "distance_km", "category", "category_key", "category_label",
    "category_confidence", "category_reason",
}


def _has_fact(value) -> bool:
    return value not in (None, "", [], {})


def _merge_event_facts(primary: dict, fallback: dict) -> dict:
    """Fill listing gaps without letting a secondary record change identity."""
    merged = dict(primary)
    for field, value in fallback.items():
        if field in _MERGE_IDENTITY_FIELDS or not _has_fact(value):
            continue
        if not _has_fact(merged.get(field)):
            merged[field] = value

    for field in ("source_links", "previous_event_ids", "discovered_via"):
        merged[field] = list(dict.fromkeys(filter(None, (
            *(primary.get(field) or []),
            *(fallback.get(field) or []),
        ))))

    primary_time = str(primary.get("time") or "")
    fallback_time = str(fallback.get("time") or "")
    fallback_range = re.fullmatch(r"(\d{2}:\d{2})–(\d{2}:\d{2})", fallback_time)
    if fallback_range and primary_time in ("", fallback_range.group(1)):
        merged["time"] = fallback_time
        for field in ("end_date", "start_at", "end_at", "all_day", "ongoing"):
            if field in fallback:
                merged[field] = fallback[field]

    return merged


def _merge_fallback_events(primary: list, fallback: list) -> list:
    positions = {
        (event.get("link") or "", event.get("title") or "", event.get("date") or ""): index
        for index, event in enumerate(primary)
    }
    merged = list(primary)
    for event in fallback:
        key = (event.get("link") or "", event.get("title") or "", event.get("date") or "")
        if key in positions:
            index = positions[key]
            merged[index] = _merge_event_facts(merged[index], event)
            continue
        merged.append(event)
        positions[key] = len(merged) - 1
    return merged


_DATED_RANGE_TITLE = re.compile(
    r"^\s*\d{1,2}\.\d{1,2}\.20\d{2}\s*[-–]\s*"
    r"\d{1,2}\.\d{1,2}\.20\d{2}\s+(.+?)"
    r"(?:\s*[-–]\s*täglich\s+ab\s+Mittagszeit)?\s*$",
    re.I,
)


def _drop_redundant_dated_title_variants(events: list) -> list:
    """Prefer Bonn's clean event record over its duplicate date-prefixed card."""
    clean_identities = {
        (
            event.get("title", "").strip().casefold(),
            event.get("city", "").strip().casefold(),
            event.get("start_date") or event.get("date", ""),
        )
        for event in events
        if not _DATED_RANGE_TITLE.match(event.get("title", ""))
    }
    kept = []
    for event in events:
        match = _DATED_RANGE_TITLE.match(event.get("title", ""))
        if match and (
            match.group(1).strip().casefold(),
            event.get("city", "").strip().casefold(),
            event.get("start_date") or event.get("date", ""),
        ) in clean_identities:
            continue
        kept.append(event)
    return kept


def _press_event_title(text: str) -> str:
    """Extract the event name before the press-release venue/date fields.

    A comma normally separates the title from the venue. Some official names
    contain a punctuation comma themselves, notably "Antik-, Kunst- &
    Designmarkt Bonn". Keep the following segment when the text before the
    first comma ends in a hyphen so the title is not truncated to "Antik-".
    """
    parts = [part.strip() for part in text.split(",")]
    if not parts:
        return ""
    if parts[0].endswith("-") and len(parts) > 1:
        return f"{parts[0]}, {parts[1]}".strip()
    return parts[0]


def _press_event_venue(text: str, title: str) -> str:
    """Keep the official location text between the title and first date."""
    remainder = text[len(title):].lstrip(" ,")
    date_start = re.search(
        r"\b\d{1,2}\.\s*(?:(?:bis|und)\s*\d{1,2}\.\s*)?"
        r"(?:Januar|Februar|März|April|Mai|Juni|Juli|August|"
        r"September|Oktober|November|Dezember)\b",
        remainder,
        re.I,
    )
    if not date_start:
        return ""
    return remainder[:date_start.start()].strip(" ,")


def _press_date_ranges(text: str, default_year: int) -> list[tuple[datetime, datetime]]:
    """Parse the date grammar used by Bonn's annual event press release."""
    month_pattern = (
        r"Januar|Februar|März|April|Mai|Juni|Juli|August|"
        r"September|Oktober|November|Dezember"
    )
    consumed: list[tuple[int, int]] = []
    ranges: list[tuple[datetime, datetime]] = []

    def add(match, start_parts, end_parts) -> None:
        try:
            start = datetime(*start_parts)
            end = datetime(*end_parts)
        except (ValueError, KeyError):
            return
        consumed.append(match.span())
        ranges.append((start, max(start, end)))

    # 27. bis 29. November 2026 / 3. und 4. Oktober 2026
    for match in re.finditer(
        rf"(\d{{1,2}})\.\s*(?:bis|und)\s*(\d{{1,2}})\.\s*"
        rf"({month_pattern})\s*(20\d{{2}})?",
        text,
        re.I,
    ):
        first, last, month_name, year_text = match.groups()
        month = common.MONTH_DE.get(month_name.casefold())
        if month:
            year = int(year_text or default_year)
            add(match, (year, month, int(first)), (year, month, int(last)))

    # 20. November bis 23. Dezember 2026
    for match in re.finditer(
        rf"(\d{{1,2}})\.\s*({month_pattern})\s*(20\d{{2}})?\s*bis\s*"
        rf"(\d{{1,2}})\.\s*({month_pattern})\s*(20\d{{2}})?",
        text,
        re.I,
    ):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        first, first_month_name, first_year, last, last_month_name, last_year = match.groups()
        first_month = common.MONTH_DE.get(first_month_name.casefold())
        last_month = common.MONTH_DE.get(last_month_name.casefold())
        if first_month and last_month:
            end_year = int(last_year or first_year or default_year)
            start_year = int(first_year or end_year)
            add(
                match,
                (start_year, first_month, int(first)),
                (end_year, last_month, int(last)),
            )

    for match in re.finditer(
        rf"(\d{{1,2}})\.\s*({month_pattern})\s*(20\d{{2}})?",
        text,
        re.I,
    ):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        day, month_name, year_text = match.groups()
        month = common.MONTH_DE.get(month_name.casefold())
        if not month:
            continue
        try:
            value = datetime(int(year_text or default_year), month, int(day))
        except ValueError:
            continue
        ranges.append((value, value))
    return sorted(set(ranges))


def fetch_press_festivals() -> list:
    """Parse the annual Bonn 'Veranstaltungsjahr' press release for district festivals.

    Each <li> looks like: "<name>, <venue...>, <date>, <date>, … <year>".
    We extract the name + every in-window date and emit one event per date. This
    surfaces Stadtteilfeste / Kirmes / markets that never reach the clean APIs —
    fully live, no event names or dates hardcoded in the script.
    """
    source = "Bonn district festivals"
    # Try this year; from October onward also try next year's edition (published early).
    years = [common.runtime_window().start.year]
    if common.runtime_window().start.month >= 10:
        years.append(common.runtime_window().start.year + 1)

    events = []
    occurrence_corrections = _active_reviewed_map(
        "bonn_press_occurrence_corrections"
    )
    primary_detail_urls = _active_reviewed_map("bonn_press_primary_urls")
    primary_event_overrides = _active_reviewed_map("bonn_press_overrides")
    for year in years:
        html = ""
        url = ""
        last_error = None
        for candidate_url in _press_urls(year):
            try:
                html = common.fetch_url(candidate_url, timeout=20)
                url = candidate_url
                break
            except Exception as exc:
                last_error = exc
        if not html:
            common.log_source_error(
                source,
                RuntimeError(f"annual press release for {year} was not found: {last_error}"),
                source_id="bonn-district-festivals",
            )
            continue
        for li in re.findall(r"<li>(.*?)</li>", html, re.S):
            text = common.clean_html(li)
            if len(text) < 6:
                continue
            title = _press_event_title(text)
            if title.casefold() == "bonn-fest":
                title = f"BonnFest {year}"
            if len(title) < 3:
                continue
            reviewed_ranges = []
            for original_start, original_end in _press_date_ranges(text, year):
                correction = occurrence_corrections.get((
                    title,
                    original_start.strftime("%Y-%m-%d"),
                    original_end.strftime("%Y-%m-%d"),
                ))
                start = (
                    datetime.fromisoformat(correction["start_date"])
                    if correction else original_start
                )
                end = (
                    datetime.fromisoformat(correction["end_date"])
                    if correction else original_end
                )
                if common.window_contains(start, end):
                    reviewed_ranges.append((start, end, correction))
            if not reviewed_ranges:
                continue
            venue = _press_event_venue(text, title)
            city = common.guess_city_from_text(text) or "Bonn"
            for start, end, correction in reviewed_ranges:
                ev = common.make_event(
                    title, start, end, venue, city,
                    correction["description"] if correction else text[:240],
                    correction["link"] if correction else url,
                    "Beuel.net" if correction else source,
                    "stadtteilfest market kirmes outdoor local", 1.0,
                    default_category_key="festival",
                )
                if ev:
                    if correction:
                        ev.update({
                            "city": correction["city"],
                            "link_kind": "detail",
                            "source_id": "beuel-net",
                            "discovered_via": ["bonn-district-festivals"],
                            "previous_event_ids": correction["previous_event_ids"],
                        })
                    occurrence_key = (
                        title,
                        start.strftime("%Y-%m-%d"),
                        end.strftime("%Y-%m-%d"),
                    )
                    primary_url = primary_detail_urls.get(occurrence_key)
                    if primary_url:
                        ev.update({
                            "link": primary_url,
                            "link_kind": "detail",
                            "source": "Bonn.de Events",
                            "source_id": "bonn-de-events",
                            "discovered_via": ["bonn-district-festivals"],
                        })
                    # Reviewed programme facts need not change source ownership.
                    # In particular an official PDF can enrich a press occurrence
                    # without pretending that it is a Bonn.de event detail.
                    ev.update(primary_event_overrides.get(occurrence_key, {}))
                    events.append(ev)
    deduped = []
    seen = set()
    for event in events:
        key = (event.get("title"), event.get("start_date"), event.get("end_date"))
        if key not in seen:
            deduped.append(event)
            seen.add(key)
    return deduped
