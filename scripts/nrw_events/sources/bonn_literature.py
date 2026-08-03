"""Literary programme in Bonn: readings, book premieres and author talks."""

import re

from .. import common
from ..category_taxonomy import CATEGORY_BY_KEY
from . import regional_common as rc


LITERATURHAUS_ICAL = "https://literaturhaus-bonn.de/veranstaltungen/?ical=1"
PARKBUCHHANDLUNG_URL = "https://www.parkbuchhandlung.de/veranstaltungen/"

# The Literaturhaus CMS drops the line break between a series label and the
# author line, so summaries arrive as "LESUNG UND PERFORMANCEMADAME NIELSEN
# »DAS ZEITGEISTERHAUS«MIT ...". Only the guillemet seams can be repaired
# deterministically; a glued "WORTREICHLUKAS" carries no detectable boundary
# and is left as published.
_MISSING_SPACE_BEFORE_WORK = re.compile(r"(?<=[^\s(\[])(»)")
_MISSING_SPACE_AFTER_WORK = re.compile(r"(«)(?=[^\s.,;:!?)\]])")
_MISSING_SPACE_BEFORE_WITH = re.compile(r"(?<=[A-ZÄÖÜ])MIT(?=\s+[A-ZÄÖÜ])")
_GLUED_SERIES_PREFIX = re.compile(
    r"^(WORTREICH|LESEZIRKEL|LESUNG UND PERFORMANCE|CUT-UP)(?=[A-ZÄÖÜ])"
)
_SOFT_HYPHEN = "­"


def _normalize_title(title: str) -> str:
    title = (title or "").replace(_SOFT_HYPHEN, "")
    title = _GLUED_SERIES_PREFIX.sub(r"\1 ", title)
    title = _MISSING_SPACE_BEFORE_WORK.sub(r" \1", title)
    title = _MISSING_SPACE_AFTER_WORK.sub(r"\1 ", title)
    title = _MISSING_SPACE_BEFORE_WITH.sub(" MIT", title)
    return re.sub(r"\s+", " ", title).strip()


def _set_category(event: dict, key: str, reason: str) -> None:
    category = CATEGORY_BY_KEY[key]
    event.update({
        "category_key": category["key"],
        "category_label": category["label"],
        "category_confidence": 1.0,
        "category_reason": reason,
    })


def _classify_literaturhaus(event: dict) -> None:
    """Classify from the official programme format, not incidental blurb words."""
    title = event.get("title", "").casefold()
    if re.search(r"spaziergang|\blit\.move\b|fahrt zur .*buchmesse", title):
        key = "outdoor"
    elif re.search(r"\bcut-up\b|collagen.*gestalten", title):
        key = "workshop"
    elif "performance" in title:
        key = "stage"
    else:
        key = "talk"
    _set_category(event, key, f"source:Literaturhaus Bonn; title-format:{key}")


def fetch_literaturhaus() -> list:
    source = "Literaturhaus Bonn"
    try:
        events = common.fetch_ical(
            LITERATURHAUS_ICAL, source, "Bonn",
            "lesung literatur autor buchvorstellung gespräch", 1.0,
            "literaturhaus-bonn",
        )
    except Exception as exc:
        common.log_source_error(source, exc)
        return []
    for event in events:
        event["title"] = _normalize_title(event.get("title", ""))
        event["venue"] = _normalize_title(event.get("venue", ""))
        if not event["venue"]:
            departure = re.search(
                r"\babfahrt\b.{0,40}?\bab\s+([^,\n.]+)",
                event.get("description", ""),
                re.I,
            )
            if departure:
                event["venue"] = _normalize_title(departure.group(1))
        _classify_literaturhaus(event)
        if not event.get("description"):
            event["description"] = common.factual_event_description(
                event.get("title", ""),
                date_value=common.parse_iso_date(event.get("start_date") or ""),
                time_text=event.get("time", ""),
                venue=event.get("venue", ""),
                city=event.get("city", "Bonn"),
            )
            event["description_source"] = "generated"
    return rc.dedupe(events)


# One card per event; the listing repeats each card in an archive block further
# down the page, which rc.dedupe collapses.
_PARK_CARD = r'(?=<div class="mkdf-event-content)'
_PARK_DATE = re.compile(r'mkdf-event-date">\s*([^<]+?)\s*<')
_PARK_LOCATION = re.compile(r'mkdf-event-location">\s*([^<]*?)\s*<')
_PARK_TITLE = re.compile(
    r'mkdf-event-title">\s*<a href="([^"]+)"[^>]*>\s*(.*?)\s*</a>', re.S
)
_PARK_DETAIL_TIME = re.compile(r'mkdf-event-header-time">\s*(\d{1,2}:\d{2})\s*<')
_PARK_DETAIL_PRICE = re.compile(r'mkdf-event-header-price">\s*([^<]+?)\s*<')
_PARK_DETAIL_CONTENT = re.compile(
    r'mkdf-event-content-holder">(.*?)(?:<div class="mkdf-grid-row"|'
    r'<div class="mkdf-event-details-holder")',
    re.S,
)


def _park_detail(html: str) -> dict[str, str]:
    time_match = _PARK_DETAIL_TIME.search(html or "")
    price_match = _PARK_DETAIL_PRICE.search(html or "")
    description = ""
    content_match = _PARK_DETAIL_CONTENT.search(html or "")
    if content_match:
        for paragraph in re.findall(r"<p\b[^>]*>(.*?)</p>", content_match.group(1), re.S):
            candidate = common.concise_description(common.clean_html(paragraph))
            if len(candidate) >= 40:
                description = candidate
                break
    return {
        "time": time_match.group(1) if time_match else "",
        "price": rc.clean(price_match.group(1)) if price_match else "",
        "description": description,
    }


def events_from_parkbuchhandlung_html(html: str, detail_fetcher=None) -> list:
    events = []
    detail_cache = {}
    for card in re.split(_PARK_CARD, html or "")[1:]:
        title_match = _PARK_TITLE.search(card)
        date_match = _PARK_DATE.search(card)
        if not (title_match and date_match):
            continue
        start = common.parse_date(rc.clean(date_match.group(1)))
        title = _normalize_title(common.clean_html(title_match.group(2)))
        if not title or start is None:
            continue
        location = _PARK_LOCATION.search(card)
        venue = _normalize_title(common.clean_html(location.group(1))) if location else ""
        city = common.refine_city_from_text("Bonn-Bad Godesberg", venue)
        link = title_match.group(1)
        detail = {}
        if detail_fetcher and common.window_contains(start):
            if link not in detail_cache:
                try:
                    detail_cache[link] = _park_detail(detail_fetcher(link))
                except Exception as exc:
                    common.log_source_error("Parkbuchhandlung", exc)
                    detail_cache[link] = {}
            detail = detail_cache[link]
        start_with_time = rc.with_time(start, detail.get("time", ""))
        event = common.make_event(
            title, start_with_time, start_with_time, venue, city,
            detail.get("description") or common.factual_event_description(
                title, date_value=start_with_time, time_text=detail.get("time", ""),
                venue=venue, city=city,
            ),
            link, "Parkbuchhandlung",
            "lesung literatur autor buchvorstellung gespräch", 1.0,
            time_text=detail.get("time", ""),
        )
        if event:
            if detail.get("price"):
                event["price"] = detail["price"].replace("€", " €").strip()
            # The publisher calendar mixes readings with occasional concerts
            # and stage formats. Preserve those explicit formats; otherwise
            # literary titles such as "Leibspeisen" must remain talks.
            key = (
                event.get("category_key")
                if event.get("category_key") in {"concert", "stage"}
                else "talk"
            )
            _set_category(event, key, f"source:Parkbuchhandlung; title-format:{key}")
            events.append(event)
    return rc.dedupe(events)


def fetch_parkbuchhandlung() -> list:
    return rc.fetch_html_events(
        "Parkbuchhandlung", PARKBUCHHANDLUNG_URL,
        lambda html: events_from_parkbuchhandlung_html(html, common.fetch_url),
     source_id="parkbuchhandlung")
