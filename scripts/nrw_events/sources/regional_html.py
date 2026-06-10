"""Municipal HTML calendars in Bonn/Rhein-Sieg."""

import re
import urllib.parse

from .. import common
from . import regional_common as rc

_TIME_PAGE_SOURCES = [
    (
        "Alfter",
        "https://www.alfter.de/schnellzugriff/veranstaltungen/",
        "Alfter",
        "alfter lokal kultur markt fest",
        0.84,
        r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
    ),
    (
        "Lohmar",
        "https://www.lohmar.de/erlebnisfaktoren-natur-und-sport-freizeit-und-tourismus/veranstaltungen/",
        "Lohmar",
        "lohmar lokal natur kultur markt",
        0.84,
        r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
    ),
]


def fetch() -> list:
    events = []
    events.extend(rc.fetch_html_events(
        "Swisttal",
        "https://www.swisttal.de/veranstaltungen/",
        _events_from_swisttal,
    ))
    for name, url, city, category, trust, pattern in _TIME_PAGE_SOURCES:
        events.extend(_fetch_time_page(url, name, city, category, trust, pattern))
    events.extend(rc.fetch_html_events(
        "Bornheim",
        "https://www.bornheim.de/veranstaltungskalender",
        _events_from_bornheim,
    ))
    events.extend(rc.fetch_html_events(
        "Eitorf",
        "https://www.eitorf.de/veranstaltungen/",
        lambda html: _events_from_eitorf_cards(html, "https://www.eitorf.de"),
    ))
    events.extend(rc.fetch_html_events(
        "Bröltal / Ruppichteroth",
        "https://www.broeltal.de/aktuelles/termine.html",
        lambda html: _events_from_broeltal(html, "https://www.broeltal.de"),
    ))
    return rc.dedupe(events)


def _fetch_time_page(url: str, source: str, city: str, category: str, trust: float,
                     title_pattern: str = "") -> list:
    def parse(html: str) -> list:
        base = urllib.parse.urlsplit(url)._replace(path="").geturl()
        return common.events_from_time_listing(
            html,
            source,
            city,
            category,
            trust,
            base,
            min_title=3,
            max_chars=1800,
            anchor_pattern=title_pattern or None,
        )

    return rc.fetch_html_events(source, url, parse)


def _events_from_swisttal(html: str) -> list:
    events = []
    for block in re.findall(r'<article class="event-template.*?</article>', html, re.S | re.I):
        date = re.search(r'<time[^>]+datetime="([^"]+)"', block, re.S | re.I)
        title = re.search(r'class="post-title"[^>]*>(.*?)</a>', block, re.S | re.I)
        href = re.search(r'class="post-title"[^>]+href="([^"]+)"', block, re.S | re.I)
        venue = re.search(r'class="host-location">\s*(.*?)\s*</div>', block, re.S | re.I)
        if not (date and title):
            continue
        ev = common.make_event(
            rc.clean(title.group(1)),
            common.parse_date(date.group(1)),
            None,
            rc.clean(venue.group(1) if venue else ""),
            "Swisttal",
            rc.clean(block),
            href.group(1) if href else "https://www.swisttal.de/veranstaltungen/",
            "Swisttal",
            "swisttal lokal markt kultur konzert fest",
            0.86,
        )
        if ev:
            events.append(ev)
    return events


def _events_from_bornheim(html: str) -> list:
    events = []
    for part in re.split(r'(?=<article class="event-teaser")', html):
        if 'class="event-teaser"' not in part:
            continue
        dates = re.findall(r'date-card-btn-date">([^<]+)', part, re.S | re.I)
        title = re.search(r'<p>([^<]{4,160})</p>', part, re.S | re.I)
        href = re.search(r'<a[^>]+href="([^"]*/veranstaltung/veranstaltung/[^"]+)"', part, re.S | re.I)
        cat = " ".join(rc.clean(x) for x in re.findall(r'<span class="eventcategory">(.*?)</span>', part, re.S | re.I))
        if not dates:
            continue
        title_text = rc.clean(title.group(1)) if title else rc.title_from_href(href.group(1) if href else "")
        if not title_text:
            continue
        for date_text in dates:
            ev = common.make_event(
                title_text,
                rc.parse_dt(date_text),
                None,
                "",
                "Bornheim",
                rc.clean(part),
                rc.abs_url("https://www.bornheim.de", href.group(1) if href else ""),
                "Bornheim",
                f"bornheim {cat} lokal markt kultur natur",
                0.86,
            )
            if ev:
                events.append(ev)
    return events


def _events_from_eitorf_cards(html: str, base: str) -> list:
    events = []
    for block in re.findall(r'<a[^>]+class="[^"]*card[^"]*"[^>]+data-date="[^"]+".*?</a>', html, re.S | re.I):
        href = re.search(r'href="([^"]+)"', block, re.I)
        date = re.search(r'data-date="([^"]+)"', block, re.I)
        title = re.search(r'<p class="title">(.*?)</p>', block, re.S | re.I)
        place = re.search(r'<p class="subtitle event-place">(.*?)</p>', block, re.S | re.I)
        subtitle = re.search(r'<p class="subtitle">\s*(.*?)\s*</p>', block, re.S | re.I)
        if not (date and title):
            continue
        start = rc.with_time(common.parse_iso_date(date.group(1)), rc.clean(subtitle.group(1) if subtitle else ""))
        ev = common.make_event(
            rc.clean(title.group(1)),
            start,
            start,
            rc.clean(place.group(1) if place else ""),
            "Eitorf",
            rc.clean(block),
            rc.abs_url(base, href.group(1) if href else ""),
            "Eitorf",
            "lokal markt kultur outdoor fest",
            0.88,
            rc.time_text(rc.clean(block)),
        )
        if ev:
            events.append(ev)
    return events


def _events_from_broeltal(html: str, base: str) -> list:
    events = []
    blocks = re.findall(r'<a class="list-group-item list-group-item-action" href="([^"]+)">(.*?)</a>',
                        html, re.S | re.I)
    for href, body in blocks:
        text = rc.clean(body)
        date_match = re.search(r"\d{1,2}\.\d{1,2}\.(?:\s*–\s*\d{1,2}\.\d{1,2}\.)?20\d{2}", text)
        title = re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', body, re.S | re.I)
        title_text = rc.clean(title.group(1)) if title else re.sub(r"\d{1,2}\.\d{1,2}\..*", "", text).strip()
        if not (date_match and title_text):
            continue
        ev = common.make_event(
            title_text,
            rc.with_time(rc.parse_dt(date_match.group(0)), text),
            None,
            "",
            "Ruppichteroth",
            text[:500],
            rc.abs_url(base, href),
            "Bröltal / Ruppichteroth",
            "broeltal ruppichteroth lokal natur markt fest",
            0.86,
            rc.time_text(text),
        )
        if ev:
            events.append(ev)
    return events
