"""Heimatmuseum Beuel — official event calendar and event details."""

import re
from datetime import datetime

from .. import common
from . import regional_common as rc

_URL = "https://www.hgv-beuel.de/aktuelles"
_SOURCE = "Heimatmuseum Beuel"
_SOURCE_ID = "hgv-beuel"
_VENUE = "Heimatmuseum Beuel"
_VENUE_ADDRESS = "Wagnergasse 2-4, 53225 Bonn"
_SAMBA_BOM_REPLACEMENT_URL = (
    "https://www.hgv-beuel.de/12-06-2026-samba-bom-ein-ausflug-in-brasiliens-musikalische-matrix"
)

_ARTICLE = re.compile(
    r'<article\b[^>]*class="[^"]*\bpost\b[^"]*"[^>]*>(.*?)</article>',
    re.S | re.I,
)
_TITLE = re.compile(
    r'<h2\b[^>]*class="[^"]*\bentry-title\b[^"]*"[^>]*>\s*'
    r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S | re.I,
)
_EXCERPT = re.compile(
    r'<div\b[^>]*class="[^"]*\bexcerpt\b[^"]*"[^>]*>(.*?)</div>',
    re.S | re.I,
)
_DATED_TITLE = re.compile(
    r"^(?:(Entfällt):\s*)?(\d{1,2}\.\d{1,2}\.20\d{2})\s*:\s*(.+)$",
    re.I,
)
_ORGANIZER_SUFFIX = re.compile(
    r"\s*\(Eine Veranstaltung der Brotfabrik(?:[- ]Bühne)?(?: mit dem Brotfabrik(?:[- ]?Chor)?)?\s*\)\s*$",
    re.I,
)
_CONCERT_PREFIX = re.compile(r"^Konzert\s*[„\"]?\s*:?\s*", re.I)
_HISTORICAL_WEATHER_POSTPONEMENT = re.compile(
    r"(?:^|\n)Wegen der schlechten Wetteraussichten wurde die Veranstaltung verschoben\.?",
    re.I,
)


def _clean_title(title: str) -> tuple[datetime | None, str, bool]:
    text = rc.clean(title)
    match = _DATED_TITLE.match(text)
    if not match:
        return None, "", False
    start = common.parse_date(match.group(2))
    event_title = _ORGANIZER_SUFFIX.sub("", match.group(3)).strip()
    event_title = _CONCERT_PREFIX.sub("", event_title).strip(' "“”„')
    return start, event_title, bool(match.group(1))


def _entry_content(html: str) -> str:
    parser = rc.ClassScopedTextParser({
        "content": lambda tag, attrs: (
            tag == "div" and "entry-content" in (attrs.get("class") or "").split()
        ),
    })
    parser.feed(html or "")
    return parser.block_text("content")


def _detail_facts(html: str) -> dict[str, str]:
    content = _entry_content(html)
    time_match = re.search(
        r"\bWann:\s*[^\n]*?\b(\d{1,2})(?:(?::|\.)(\d{2}))?\s*Uhr\b",
        content,
        re.I,
    )
    time_text = ""
    if time_match:
        time_text = f"{int(time_match.group(1)):02d}:{int(time_match.group(2) or 0):02d}"

    location_match = re.search(
        r"(?:^|\n)(?:Wo|Veranstaltungsort):\s*([^\n]+)",
        content,
        re.I,
    )
    location = rc.clean(location_match.group(1)) if location_match else ""

    price = ""
    if free_price := common.infer_free_admission_price("", content):
        price = free_price
    else:
        price_match = re.search(
            r"(?:Anmeldung:\s*)?Vorverkauf[^\n]*?:\s*([^\n.]+€(?:\s*,\s*ermäßigt\s*[^\n.]+€)?)",
            content,
            re.I,
        )
        if price_match:
            price = re.sub(r"\s+", " ", price_match.group(1)).strip(" .")

    return {
        "description": common.concise_description(content, max_chars=700),
        "time": time_text,
        "location": location,
        "price": price,
    }


def events_from_html(html: str, detail_fetcher=None) -> list:
    events = []
    for article in _ARTICLE.findall(html or ""):
        title_match = _TITLE.search(article)
        if not title_match:
            continue
        start, title, cancelled = _clean_title(title_match.group(2))
        if start is None or not title:
            continue

        link = rc.abs_url(_URL, title_match.group(1))
        excerpt_match = _EXCERPT.search(article)
        excerpt = common.concise_description(
            rc.clean(excerpt_match.group(1) if excerpt_match else ""),
            max_chars=360,
        )
        detail: dict[str, str] = {}
        if detail_fetcher and common.window_contains(start):
            try:
                detail = _detail_facts(detail_fetcher(link))
                # This one page carries its new September date in both the title
                # and "Wann" field, followed by a stale note saying it "wurde"
                # verschoben. Scope the cleanup to that reviewed replacement;
                # the same sentence on another occurrence remains a real status.
                if (
                    link.rstrip("/") == _SAMBA_BOM_REPLACEMENT_URL
                    and start.strftime("%Y-%m-%d") == "2026-09-04"
                ):
                    detail["description"] = _HISTORICAL_WEATHER_POSTPONEMENT.sub(
                        "", detail.get("description", "")
                    ).strip()
            except Exception as exc:
                common.log_source_error(f"{_SOURCE} detail", exc, source_id=_SOURCE_ID)

        time_text = detail.get("time", "")
        start_with_time = rc.with_time(start, time_text)
        description = detail.get("description") or excerpt or common.factual_event_description(
            title,
            date_value=start_with_time,
            time_text=time_text,
            venue=_VENUE,
            city="Bonn",
        )
        if cancelled:
            description = f"Die Veranstaltung entfällt. {description}"
        event = common.make_event(
            title,
            start_with_time,
            start_with_time,
            _VENUE,
            "Bonn",
            description,
            link,
            _SOURCE,
            "heimatmuseum beuel kultur konzert lesung theater familie tradition",
            1.0,
            time_text=time_text,
            source_id=_SOURCE_ID,
            description_source="scraped" if detail.get("description") or excerpt else "generated",
        )
        if event:
            event["venue_address"] = _VENUE_ADDRESS
            event["organizer"] = "Heimat- und Geschichtsverein Beuel am Rhein e.V."
            if detail.get("description"):
                event["_detail_page_enriched"] = True
            if location := detail.get("location"):
                event["venue_note"] = location
            if price := detail.get("price"):
                event["price"] = price
                event["admission_basis"] = "explicit"
            events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda html: events_from_html(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url,
                cache_namespace=_SOURCE_ID,
                timeout=20,
            ),
        ),
        source_id=_SOURCE_ID,
    )
