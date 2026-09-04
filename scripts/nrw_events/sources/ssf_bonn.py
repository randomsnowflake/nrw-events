"""First-party events from Schwimm- und Sportfreunde Bonn."""

from __future__ import annotations

import re

from .. import common
from . import regional_common as rc

URL = "https://www.ssfbonn.de/de/aktuelles/veranstaltungen/ssf-festival/"
SOURCE = "SSF Bonn"
SOURCE_ID = "ssf-bonn"


def events_from_page(html: str) -> list:
    """Parse the current SSF Festival occurrence from its first-party page."""
    events = []
    headings = list(re.finditer(
        r'<div[^>]+class=["\'][^"\']*\bheadline2\b[^"\']*["\'][^>]*>'
        r'\s*(\d+\.\s*SSF Festival)\s*</div>',
        html or "",
        re.I | re.S,
    ))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(html or "")
        block = (html or "")[heading.end():end]
        occurrence = re.search(
            r'\bam\s+(\d{1,2}\.\d{1,2}\.20\d{2})\s+auf dem\s+([^<]+)',
            block,
            re.I,
        )
        if not occurrence:
            continue
        start = common.parse_date(occurrence.group(1))
        if not start:
            continue
        text = rc.clean(block)
        time_match = re.search(
            r'\b(?:von\s+)?(\d{1,2}:\d{2})\s*Uhr\s+bis\s+(\d{1,2}:\d{2})\s*Uhr',
            text,
            re.I,
        )
        time_text = ""
        end_at = start
        if time_match:
            time_text = f"{time_match.group(1)}–{time_match.group(2)}"
            start = rc.with_time(start, time_match.group(1))
            end_at = rc.with_time(end_at, time_match.group(2))
        venue = rc.clean(occurrence.group(2)).strip(" .") or "Münsterplatz"
        edition_title = rc.clean(heading.group(1))
        description = common.concise_description(f"{edition_title}. {text}", max_chars=420)
        event = common.make_event(
            "SSF Festival",
            start,
            end_at,
            venue,
            "Bonn",
            description,
            URL,
            SOURCE,
            "sport vereinsfest festival familie mitmachen musik",
            1.0,
            time_text=time_text,
            source_id=SOURCE_ID,
            description_source="scraped",
            default_category_key="festival",
            category_locked=True,
        )
        if event:
            events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    return rc.fetch_html_events(
        SOURCE,
        URL,
        events_from_page,
        source_id=SOURCE_ID,
    )
