"""Literary programme in Bonn: readings, book premieres and author talks."""

import re

from .. import common
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
_SOFT_HYPHEN = "­"


def _normalize_title(title: str) -> str:
    title = (title or "").replace(_SOFT_HYPHEN, "")
    title = _MISSING_SPACE_BEFORE_WORK.sub(r" \1", title)
    title = _MISSING_SPACE_AFTER_WORK.sub(r"\1 ", title)
    return re.sub(r"\s+", " ", title).strip()


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
        if not event.get("description"):
            event["description"] = common.factual_event_description(
                event.get("title", ""),
                date_value=common.parse_iso_date(event.get("start_date") or ""),
                time_text=event.get("time", ""),
                venue=event.get("venue", ""),
                city=event.get("city", "Bonn"),
            )
    return rc.dedupe(events)


# One card per event; the listing repeats each card in an archive block further
# down the page, which rc.dedupe collapses.
_PARK_CARD = r'(?=<div class="mkdf-event-content)'
_PARK_DATE = re.compile(r'mkdf-event-date">\s*([^<]+?)\s*<')
_PARK_LOCATION = re.compile(r'mkdf-event-location">\s*([^<]*?)\s*<')
_PARK_TITLE = re.compile(
    r'mkdf-event-title">\s*<a href="([^"]+)"[^>]*>\s*(.*?)\s*</a>', re.S
)


def events_from_parkbuchhandlung_html(html: str) -> list:
    events = []
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
        event = common.make_event(
            title, start, start, venue, city,
            common.factual_event_description(
                title, date_value=start, venue=venue, city=city,
            ),
            title_match.group(1), "Parkbuchhandlung",
            "lesung literatur autor buchvorstellung gespräch", 1.0,
            all_day=True,
        )
        if event:
            events.append(event)
    return rc.dedupe(events)


def fetch_parkbuchhandlung() -> list:
    return rc.fetch_html_events(
        "Parkbuchhandlung", PARKBUCHHANDLUNG_URL, events_from_parkbuchhandlung_html
    )
