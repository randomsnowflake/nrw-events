"""MagicCon and FedCon dates published by their Bonn organizer."""

from __future__ import annotations

import re

from .. import common
from . import regional_common as rc

SOURCE = "FedCon Events"
URLS = ("https://www.magiccon.de/de/", "https://www.fedcon.de/de/")


def _event_from_page(html: str, link: str):
    # The current MagicCon homepage keeps the date in its description metadata,
    # with a shared month (02.-04.10.2026). Keep each evidence block separate so
    # historical navigation titles cannot capture a later occurrence's date.
    match = None
    for text in (rc.meta_description(html), common.clean_html(html)):
        match = re.search(
            r"\b(?P<title>(?:MagicCon|FedCon)\s+\d+)\b.{0,200}?\bvom\s+"
            r"(?P<day>\d{1,2})\.(?:(?P<month>\d{1,2})\.)?\s*[-–—]\s*"
            r"(?P<end>\d{1,2}\.(?P<end_month>\d{1,2})\.(?P<year>20\d{2}))\b"
            r".{0,200}?\bMaritim Hotel Bonn\b",
            text, re.I,
        )
        if match:
            break
    if not match:
        return None
    month = match.group("month") or match.group("end_month")
    start = common.parse_date(f"{match.group('day')}.{month}.{match.group('year')}")
    end = common.parse_date(match.group("end"))
    if start is None or end is None or end < start:
        return None
    title = match.group("title")
    description = common.factual_event_description(title, date_value=start, venue="Maritim Hotel Bonn", city="Bonn", calendar_name="FedCon Events")
    return common.make_event(title, start, end, "Maritim Hotel Bonn", "Bonn", description, link, SOURCE, "convention science fiction fantasy cosplay festival", 1.0, source_id="fedcon-events", description_source="generated", all_day=True)


def fetch() -> list:
    events, successful_pages = [], 0
    for url in URLS:
        try:
            html = common.fetch_url(url, timeout=25)
            successful_pages += 1
            with common.capture_parser_metrics() as metrics:
                event = _event_from_page(html, url)
            if event:
                events.append(event)
            elif metrics["out_of_window_count"] == 0:
                common.log_source_error(SOURCE, rc.ParserEmptyError(f"parser returned no event record for {url}"))
        except Exception as exc:  # noqa: PERF203 - organizer pages must fail independently
            common.log_source_error(f"{SOURCE} {url}", exc)
    common._record_endpoint(URLS[0], parser_type="organizer-homepages", parsed_event_count=len(events), parser_empty=successful_pages > 0 and not events)
    return rc.dedupe(events)
