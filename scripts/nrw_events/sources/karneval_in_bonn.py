"""Festausschuss Bonner Karneval: session calendar with per-event detail pages."""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Callable, Iterator
from urllib.parse import urljoin

from .. import detail_cache, event_builder, http, location, run_state, text
from ..dates import parse_date
from . import regional_common as rc

URL = "https://karneval-in-bonn.de/aktuell/termine"
SOURCE = "Festausschuss Bonner Karneval"
SOURCE_ID = "karneval-in-bonn"

# The unfiltered list already carries every published date of the session;
# the category/district filters only return subsets of it.
_ITEM = re.compile(
    r"<div class=\"DateAndTime\">(.*?)</div></div>\s*<h3\b[^>]*>\s*"
    r"<a href='([^']+)'[^>]*>(.*?)</a>",
    re.S | re.I,
)
_DATE = re.compile(
    r"<span class='Day'>\s*(\d{1,2})\.\s*</span>\s*<span class='Month'>\s*(\w+)\s*</span>"
    r"\s*<span class='Year'>\s*(\d{4})",
    re.S | re.I,
)
_TIME = re.compile(r"<div class='Time'>\s*(\d{1,2}:\d{2})", re.I)
_INVITATION_ONLY = re.compile(r"\bauf\s+(?:(?:besondere|persönliche)\s+)?einladung\b", re.I)
_CARD = re.compile(r"<article\b[^>]*>(.*?)</article>", re.S | re.I)


def _listing_rows(page: str) -> Iterator[tuple[str, str, str, str, dict[str, str] | None]]:
    for card in _CARD.findall(page):
        heading = re.search(r'<h3\b[^>]*>\s*<a href="(/aktuell/termine/[^"/]+)"[^>]*>(.*?)</a>',
                            card, re.S | re.I)
        when = re.search(r'(\d{2}\.\d{2}\.\d{4})(?:, (\d{2}:\d{2}) Uhr)?', card)
        if not heading or not when:
            continue
        venue = re.search(r'<span>\s*·\s*(.*?)</span>', card, re.S)
        content = re.search(r'<div class="md mt-3[^"]*">(.*?)</div>', card, re.S)
        description = rc.clean_blocks(content.group(1)) if content else ""
        organizer = re.search(r'^Veranstalter:\s*(.+)', description, re.M)
        yield when.group(1), when.group(2) or "", urljoin(URL, heading.group(1)), heading.group(2), {
            "venue": rc.clean(venue.group(1)) if venue else "",
            "organizer": organizer.group(1).strip() if organizer else "",
            "address": "",
            "content": description,
        }
    # Retain the old layout for saved source replays, not as a network fallback.
    for date_block, href, title in _ITEM.findall(page):
        date_match = _DATE.search(date_block)
        if date_match:
            time_match = _TIME.search(date_block)
            yield (" ".join(date_match.groups()), time_match.group(1) if time_match else "",
                   html_lib.unescape(href), title, None)


def _field(page: str, class_name: str) -> str:
    match = re.search(rf'<div class="{class_name}">(.*?)</div>', page, re.S | re.I)
    return match.group(1) if match else ""


def _detail_facts(page: str) -> dict[str, str]:
    address_lines = [
        line for line in rc.clean_blocks(_field(page, "Adress")).splitlines() if line.strip()
    ]
    return {
        "venue": rc.clean(_field(page, "Location")),
        "organizer": rc.clean(_field(page, "Organizer")),
        "address": ", ".join(address_lines),
        "content": rc.clean_blocks(_field(page, "Row Content")).strip(),
    }


def _title(listing_title: str, organizer: str) -> str:
    title = rc.clean(listing_title)
    if organizer and title.endswith(f" - {organizer}"):
        return title[: -len(organizer) - 3].strip()
    # Listing titles are "<event> - <organizer>"; organizer spellings on the
    # detail page sometimes differ, so fall back to the first separator.
    return title.split(" - ", 1)[0].strip()


def events_from_html(page: str, detail_fetcher: Callable[[str], str] | None = None) -> list:
    events = []
    for date_text, time_text, link, listing_title, listing_detail in _listing_rows(page or ""):
        detail = listing_detail
        start = parse_date(date_text)
        start = rc.with_time(start, time_text)
        if start is None:
            continue
        # Each detail page costs one crawl-delayed request: fetch in-window rows only.
        if not event_builder.window_contains(start):
            continue
        if detail is None:
            if detail_fetcher is None:
                continue
            try:
                detail = _detail_facts(detail_fetcher(link))
            except Exception as exc:
                run_state.log_source_error(f"{SOURCE} detail", exc, source_id=SOURCE_ID)
                continue
        # Invitation-only receptions and rows without a hall are not public events.
        if not detail["venue"] or _INVITATION_ONLY.search(detail["content"]):
            continue
        title = _title(listing_title, detail["organizer"])
        city = location.refine_city_from_text("Bonn", detail["address"])
        description = detail["content"]
        event = event_builder.make_event(
            title,
            start,
            start,
            detail["venue"],
            city,
            description or text.factual_event_description(
                title, date_value=start, time_text=time_text, venue=detail["venue"], city=city,
            ),
            link,
            SOURCE,
            # No "sitzung" here: the governance junk rule reads it as a council meeting.
            "karneval brauchtum feste",
            1.0,
            time_text=time_text,
            source_id=SOURCE_ID,
            description_source="scraped" if description else "generated",
            # Every row is a carnival date; prose naming the Kinder-Prinzenpaar
            # must not turn the 11.11. opening into a kids event.
            default_category_key="festival",
            category_locked=True,
            link_kind="detail",
        )
        if not event:
            continue
        event["organizer"] = detail["organizer"]
        if detail["address"]:
            event["venue_address"] = detail["address"]
        if price := event_builder.infer_free_admission_price(title, description):
            event["price"] = price
            event["admission_basis"] = "explicit"
        # Already detail-backed: keep the shared pass from refetching it (10 s each).
        event["_detail_page_enriched"] = True  # type: ignore[typeddict-unknown-key]
        events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list:
    return rc.fetch_html_events(
        SOURCE,
        URL,
        lambda page: events_from_html(
            page,
            detail_fetcher=lambda url: detail_cache.fetch_detail_url(
                url, cache_namespace=SOURCE_ID, timeout=20,
            ),
        ),
        source_id=SOURCE_ID,
        fetcher=http.fetch_url,
        # A recognizable calendar whose rows are all outside the window or
        # invitation-only is a healthy empty result, not parser drift.
        empty_is_healthy=lambda page: any(_listing_rows(page or "")),
    )
