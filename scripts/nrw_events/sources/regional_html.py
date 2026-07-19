"""Municipal HTML calendars in Bonn/Rhein-Sieg."""

import re
import urllib.parse

from .. import common
from . import regional_common as rc

_LOHMAR_BASE_URL = "https://www.lohmar.de/"
_LOHMAR_CALENDAR_URL = urllib.parse.urljoin(
    _LOHMAR_BASE_URL,
    "erlebnisfaktoren-natur-und-sport-freizeit-und-tourismus/veranstaltungen/",
)


def fetch() -> list:
    events = []
    events.extend(rc.fetch_html_events(
        "Swisttal",
        "https://www.swisttal.de/veranstaltungen/",
        _events_from_swisttal,
    ))
    events.extend(_fetch_alfter())
    events.extend(rc.fetch_html_events(
        "Lohmar",
        _LOHMAR_CALENDAR_URL,
        lambda html: _events_from_lohmar(
            html,
            detail_fetcher=lambda link: common.fetch_detail_url(
                link, cache_namespace="lohmar", timeout=15),
        ),
    ))
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


def _fetch_alfter() -> list:
    url = "https://www.alfter.de/schnellzugriff/veranstaltungen/"

    def parse(html: str) -> list:
        base = urllib.parse.urlsplit(url)._replace(path="").geturl()
        return common.events_from_time_listing(
            html,
            "Alfter",
            "Alfter",
            "alfter lokal kultur markt fest",
            0.84,
            base,
            min_title=3,
            max_chars=1800,
            anchor_pattern=r'<h3>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
        )

    return rc.fetch_html_events("Alfter", url, parse)


def _events_from_lohmar(html: str, detail_fetcher=None) -> list:
    """Parse Lohmar's event cards, including their teaser, time, and venue.

    The generic time-listing parser only retains the date/title/link tuple.
    Lohmar renders richer fields in each server-side card, so use those directly
    and request the detail page only when a teaser is genuinely missing.
    """
    events = []
    blocks = re.split(
        r'(?=<div[^>]+class="[^"]*\barticle\b[^"]*"[^>]*>)',
        html or "",
        flags=re.I,
    )
    for block in blocks:
        time_match = re.search(
            r'<time[^>]+datetime="([^"]+)"[^>]*>(.*?)</time>',
            block,
            re.S | re.I,
        )
        title_match = re.search(
            r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>',
            block,
            re.S | re.I,
        )
        if not (time_match and title_match):
            continue

        title = rc.clean(title_match.group(2))
        link = rc.abs_url(_LOHMAR_BASE_URL, title_match.group(1))
        time_text = rc.time_text(rc.clean(time_match.group(2)))
        venue_match = re.search(
            r'Veranstaltungsort:\s*(.*?)(?:</div>|<br\s*/?>)',
            block,
            re.S | re.I,
        )
        venue = rc.clean(venue_match.group(1) if venue_match else "")

        teaser_match = re.search(
            r'<div[^>]+class="[^"]*\bteaser-text\b[^"]*"[^>]*>(.*?)</div>',
            block,
            re.S | re.I,
        )
        teaser_html = teaser_match.group(1) if teaser_match else ""
        teaser_html = re.sub(
            r'<a\b[^>]*>\s*(?:mehr|details?)\s*</a>',
            "",
            teaser_html,
            flags=re.S | re.I,
        )
        description = common.concise_description(rc.clean(teaser_html))
        teaser_is_title = (
            description.casefold().strip(" .") == title.casefold().strip(" .")
        )
        start = common.parse_iso_date(time_match.group(1))
        if (not description or teaser_is_title) and common.window_contains(start):
            description = _lohmar_detail_description(link, detail_fetcher)
        description = description or _lohmar_fallback_description(title, time_text, venue)

        source_categories = " ".join(
            rc.clean(value)
            for value in re.findall(
                r'class="[^"]*\beventcategory\b[^"]*"[^>]*title="([^"]+)"',
                block,
                re.S | re.I,
            )
        )
        event = common.make_event(
            title,
            start,
            None,
            venue,
            "Lohmar",
            description,
            link,
            "Lohmar",
            f"lohmar lokal natur kultur markt {source_categories}",
            0.84,
            time_text,
        )
        if event:
            events.append(event)
    return events


def _lohmar_detail_description(link: str, detail_fetcher) -> str:
    if not (link and detail_fetcher):
        return ""
    try:
        html = detail_fetcher(link)
    except Exception as exc:
        common.log_source_error("Lohmar detail", exc)
        return ""
    body = re.search(
        r'<div[^>]+class="[^"]*\bnews-text-wrap\b[^"]*"[^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(rc.clean(body.group(1) if body else ""))


def _lohmar_fallback_description(title: str, time_text: str, venue: str) -> str:
    details = ""
    if time_text:
        details += f" um {time_text} Uhr"
    if venue:
        details += f" am Veranstaltungsort „{venue}“"
    return f"„{title}“ ist im Lohmarer Veranstaltungskalender{details} angekündigt."


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
        start, end = rc.range_dates(text)
        title = re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', body, re.S | re.I)
        title_text = rc.clean(title.group(1)) if title else re.sub(r"\d{1,2}\.\d{1,2}\..*", "", text).strip()
        if not (start and title_text):
            continue
        ev = common.make_event(
            title_text,
            rc.with_time(start, text),
            end,
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
