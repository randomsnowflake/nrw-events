"""First-party b° future festival programme in Bonn."""

from __future__ import annotations

import re
import urllib.parse

from .. import common
from . import regional_common as rc


URL = "https://www.b-future.org/2026/programm"
SOURCE = "b° future festival"


def _field(block: str, class_name: str) -> str:
    block = re.sub(r"<svg\b.*?</svg>", " ", block or "", flags=re.S | re.I)
    match = re.search(
        rf'<[^>]+class=["\'][^"\']*\b{re.escape(class_name)}\b[^"\']*["\'][^>]*>(.*?)</[^>]+>',
        block, re.S | re.I,
    )
    return common.clean_html_blocks(match.group(1)) if match else ""


def _events_from_program(html: str) -> list:
    events = []
    day_matches = list(re.finditer(r'<h2[^>]*>\s*<time[^>]+datetime=["\'](20\d{2}-\d{2}-\d{2})["\']', html or "", re.I))
    for index, day_match in enumerate(day_matches):
        day_html = (html or "")[day_match.end():day_matches[index + 1].start() if index + 1 < len(day_matches) else len(html or "")]
        for article_match in re.finditer(r'<article\b[^>]*class=["\'][^"\']*\bevent-list-item\b[^"\']*["\'][^>]*>(.*?)</article>', day_html, re.S | re.I):
            block = article_match.group(1)
            title = _field(block, "event-list-item__headline")
            if not title:
                continue
            times = re.findall(r'<time[^>]+datetime=["\']([0-2]\d:[0-5]\d)["\']', block, re.I)
            start = common.parse_iso_date(day_match.group(1))
            end = start
            if start and times:
                hour, minute = map(int, times[0].split(":"))
                start = start.replace(hour=hour, minute=minute)
                if len(times) > 1:
                    hour, minute = map(int, times[1].split(":"))
                    end = end.replace(hour=hour, minute=minute)
            room = re.sub(r"^\s*//\s*", "", _field(block, "event-list-item__room")).strip()
            location = _field(block, "event-list-item__location")
            venue = location or room
            description = _field(block, "event-list-item__description")
            if not description:
                description = common.factual_event_description(title, date_value=start, venue=venue, city="Bonn", calendar_name="b° future festival")
            link_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', block, re.I)
            link = urllib.parse.urljoin(URL, link_match.group(1)) if link_match else URL
            if location and room and room.casefold() != location.casefold():
                description = common.concise_description(f"Bereich/Raum: {room}. {description}")
            title_words = title.casefold()
            default_category = (
                "workshop" if any(word in title_words for word in ("workshop", "deep dive", "clinic", "coaching")) else
                "activities" if any(word in title_words for word in ("quiz", "mitmachen", "ausprobieren")) else
                "festival" if any(word in title_words for word in ("opening ceremony", "welcome night", "festival")) else
                "talk"
            )
            event = common.make_event(title, start, end or start, venue, "Bonn", description, link, SOURCE, "journalism festival conference workshop talk panel", 1.0, source_id="b-future-festival", description_source="scraped" if _field(block, "event-list-item__description") else "generated", default_category_key=default_category, category_locked=True)
            if not event:
                continue
            ticket = _field(block, "event-list-item__ticket")
            if "festivalticket" in ticket.casefold():
                event["price"] = "Festivalticket erforderlich"
                event["admission_basis"] = "explicit"
            events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    try:
        html = common.fetch_url(URL, timeout=25)
        with common.capture_parser_metrics() as metrics:
            events = _events_from_program(html)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(URL, parser_type="festival-program-html", candidate_count=metrics["candidate_count"], out_of_window_count=metrics["out_of_window_count"], parsed_event_count=len(events), parser_empty=parser_empty)
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
