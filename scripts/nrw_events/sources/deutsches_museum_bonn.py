"""Deutsches Museum Bonn — official dynamically loaded program calendar."""

import html as html_module
import re
from datetime import datetime

from .. import common
from . import regional_common as rc

_URL = "https://www.deutsches-museum.de/bonn/programm"
_SOURCE = "Deutsches Museum Bonn"
_VENUE = "Deutsches Museum Bonn"
_PRIMARY_SOURCE_SCORE_FLOOR = 0.45


def _ajax_url(html: str) -> str:
    match = re.search(r'data-ajaxuri="([^"]*?/bonn/programm/ems/indices\.html[^"]*)"', html or "", re.I)
    return rc.abs_url(_URL, html_module.unescape(match.group(1))) if match else ""


def _article_blocks(html: str) -> list[str]:
    return re.findall(
        r'<article\b[^>]*class="[^"]*\bevents-teaser__content\b[^"]*"[^>]*>(.*?)</article>',
        html or "",
        re.S | re.I,
    )


def events_from_html(html: str) -> list:
    events = []
    for article in _article_blocks(html):
        title_match = re.search(
            r'class="[^"]*\bevents-teaser__content-title\b[^"]*"[^>]*>.*?'
            r'<a[^>]+href="([^"]+)"[^>]*>\s*(?:<span[^>]*>)?(.*?)(?:</span>)?\s*</a>',
            article,
            re.S | re.I,
        )
        time_match = re.search(r'<time[^>]+datetime="(20\d{2}-\d{2}-\d{2})\s+(\d{2}):(\d{2})"[^>]*>(.*?)</time>', article, re.S | re.I)
        if not (title_match and time_match):
            continue

        start = datetime.strptime(
            f"{time_match.group(1)} {time_match.group(2)}:{time_match.group(3)}",
            "%Y-%m-%d %H:%M",
        )
        visible_time = rc.clean(time_match.group(4))
        clocks = re.findall(r"\b(\d{1,2}):(\d{2})\b", visible_time)
        end = None
        if len(clocks) >= 2:
            end = start.replace(hour=int(clocks[-1][0]), minute=int(clocks[-1][1]))
        description_match = re.search(r'</h3>\s*(?:</header>\s*)?<p>(.*?)</p>', article, re.S | re.I)
        labels = [rc.clean(label) for label in re.findall(
            r'class="[^"]*\bevents-teaser__content-label\b[^"]*"[^>]*>\s*<span>(.*?)</span>',
            article,
            re.S | re.I,
        )]
        description = rc.clean(description_match.group(1) if description_match else "")
        if not description:
            description = " ".join(labels) or "Veranstaltung im Deutschen Museum Bonn."
        event = common.make_event(
            rc.clean(title_match.group(2)),
            start,
            end,
            _VENUE,
            "Bonn",
            description,
            rc.abs_url(_URL, title_match.group(1)),
            _SOURCE,
            "Museum",
            1.0,
            rc.time_text(visible_time) or start.strftime("%H:%M"),
            all_day=False,
        )
        if event:
            # Preserve requested first-party museum programmes even when the
            # global ranking downranks their kids-only wording.
            event["score"] = max(float(event.get("score") or 0), _PRIMARY_SOURCE_SCORE_FLOOR)
            events.append(event)
    return rc.dedupe_occurrences(events)


def _detail_description(html: str, _event: dict) -> dict:
    body = re.search(
        r'class="[^"]*\bevent-detail-text\b[^"]*"[^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    teaser = re.search(r'data-teaser-text-target[^>]*>\s*<p>(.*?)</p>', html or "", re.S | re.I)
    description = common.concise_description(
        rc.clean(body.group(1) if body else (teaser.group(1) if teaser else "")),
        max_chars=300,
    )
    facts = []
    if re.search(r"Der\s+Eintritt\s+ist\s+frei", html or "", re.I):
        facts.append("Eintritt frei.")
    if re.search(r"Die\s+Teilnahme\s+ist\s+im\s+Museumseintritt\s+enthalten", html or "", re.I):
        facts.append("Teilnahme im Museumseintritt enthalten.")
    registration = re.search(r"Nur\s+nach\s+Anmeldung[^<.]*", html or "", re.I)
    if registration:
        facts.append(rc.clean(registration.group(0)).rstrip(".") + ".")
    return {"description": " ".join(filter(None, [description, *facts]))}


def _enrich_details(events: list) -> list:
    events = rc.enrich_descriptions(
        events,
        source=_SOURCE,
        cache_namespace="deutsches-museum-bonn-v2",
        extract_context=_detail_description,
        fallback=lambda event: event.get("description", ""),
        needs_enrichment=lambda _event: True,
    )
    for event in events:
        event["price"], event["admission_basis"] = common.infer_admission(
            event["title"], event["description"], event.get("price", "")
        )
    return events


def fetch() -> list:
    try:
        page = common.fetch_url(_URL, timeout=25)
        endpoint = _ajax_url(page)
        if not endpoint:
            raise rc.ParserEmptyError("Deutsches Museum Bonn AJAX endpoint not found")
        cards = common.fetch_url(endpoint, timeout=25)
        if not _article_blocks(cards):
            raise rc.ParserEmptyError("Deutsches Museum Bonn program cards not found")
        with common.capture_parser_metrics() as metrics:
            events = events_from_html(cards)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(
            endpoint,
            parser_type="html",
            candidate_count=metrics["candidate_count"],
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events),
            parser_empty=parser_empty,
        )
        if parser_empty:
            raise rc.ParserEmptyError("Deutsches Museum Bonn program parser returned no events")
        return _enrich_details(events)
    except Exception as exc:
        common.log_source_error(_SOURCE, exc, source_id="deutsches-museum-bonn")
        return []
