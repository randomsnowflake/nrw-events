"""Kunstmuseum Bonn — official event calendar and event details."""

import re

from .. import common
from . import regional_common as rc

_URL = "https://www.kunstmuseum-bonn.de/de/besuch/kalender/"
_SOURCE = "Kunstmuseum Bonn"
_SOURCE_ID = "kunstmuseum-bonn"
_VENUE = "Kunstmuseum Bonn"


def _detail_description(html: str) -> str:
    body = re.search(
        r'<div[^>]*class="[^"]*\bpost-body\b[^"]*"[^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(
        rc.clean(body.group(1) if body else ""), max_chars=360,
    )


def _detail_price(html: str) -> str:
    """Read the visitor cost from the museum's labeled event facts."""
    match = re.search(
        r'<h[1-6][^>]*>\s*Kosten\s*</h[1-6]>\s*<p[^>]*>(.*?)</p>',
        html or "",
        re.S | re.I,
    )
    return rc.clean(match.group(1))[:160] if match else ""


def _fallback_description(title: str, format_text: str, start) -> str:
    schedule = f" am {start:%d.%m.%Y}" if start else ""
    if start and start.strftime("%H:%M") != "00:00":
        schedule += f" um {start:%H:%M} Uhr"
    format_label = format_text or "Veranstaltung"
    return (
        f"„{title}“ ist ein Angebot im Format „{format_label}“ und findet"
        f"{schedule} im Kunstmuseum Bonn statt."
    )


def events_from_html(html: str, detail_fetcher=None) -> list:
    events = []
    for href, body in re.findall(
        r'<a href="(?P<href>[^"]+/de/besuch/kalender/[^"]+/)">(.*?)</a>',
        html or "",
        re.S | re.I,
    ):
        date_match = re.search(
            r'class="teaser-date">\s*(.*?)\s*</p>', body, re.S | re.I,
        )
        title_match = re.search(
            r'class="teaser-title">\s*(.*?)\s*</h4>', body, re.S | re.I,
        )
        format_match = re.search(
            r'class="teaser-meta">\s*(.*?)\s*</p>', body, re.S | re.I,
        )
        if not (date_match and title_match):
            continue

        date_text = rc.clean(date_match.group(1))
        start = rc.with_time(common.parse_date(date_text), date_text)
        title = rc.clean(title_match.group(1))
        format_text = rc.clean(format_match.group(1) if format_match else "")
        description = ""
        price = ""
        if detail_fetcher and common.window_contains(start):
            try:
                detail_html = detail_fetcher(href)
                description = _detail_description(detail_html)
                price = _detail_price(detail_html)
            except Exception as exc:
                common.log_source_error(f"{_SOURCE} detail", exc)

        event = common.make_event(
            title,
            start,
            start,
            _VENUE,
            "Bonn",
            description or _fallback_description(title, format_text, start),
            href,
            _SOURCE,
            "museum kunst ausstellung führung workshop performance lesung konzert",
            0.92,
            source_id=_SOURCE_ID,
            description_source="scraped" if description else "generated",
        )
        if event:
            if price:
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
