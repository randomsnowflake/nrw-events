"""Nearby tourism and destination calendars around Bonn."""

import re

from .. import common
from . import regional_common as rc


def fetch() -> list:
    ahrtal_url = "https://www.ahrtal.com/de/events"
    events = []
    events.extend(rc.fetch_html_events(
        "Ahrtal",
        ahrtal_url,
        lambda html: _events_from_shapehub(
            html,
            "Ahrtal",
            "https://www.ahrtal.com",
            ahrtal_url,
            "Ahrweiler",
            "ahrtal wein wanderung führung kultur ausstellung",
            0.86,
        ),
    ))
    events.extend(rc.fetch_html_events(
        "Linz am Rhein",
        "https://www.linz.de/startseite/tourismus-freizeit/veranstaltungen",
        _events_from_linz,
    ))
    events.extend(rc.fetch_html_events(
        "Bad Münstereifel",
        "https://www.bad-muenstereifel.de/tourismus-freizeit/veranstaltungskalender",
        _events_from_bad_muenstereifel,
    ))
    events.extend(rc.fetch_html_events(
        "Kultur Euskirchen",
        "https://www.kultur-euskirchen.de/stadttheater/veranstaltungen",
        _events_from_euskirchen,
    ))
    return rc.dedupe(events)


def _events_from_shapehub(html: str, source: str, base: str, listing_url: str,
                          default_city: str, category: str, trust: float) -> list:
    events = []
    card_re = re.compile(r'<a href="(?P<href>[^"]+)" class="shapehub-card-link">(?P<body>.*?)</a>',
                         re.S | re.I)
    for m in card_re.finditer(html):
        body = m.group("body")
        date = re.search(r'shapehub-date-badge">\s*([^<]+)', body, re.S | re.I)
        title = re.search(r'shapehub-card-title">(.*?)</div>', body, re.S | re.I)
        if not (date and title):
            continue
        text = rc.clean(body)
        city = rc.city_from_text(text, default_city)
        start = rc.parse_dt(date.group(1))
        detail_url = rc.abs_url(base, m.group("href"))
        # Shapehub removes detail pages at the start of their event date (HTTP
        # 410) while leaving the cards in the calendar. Keep today's cards
        # useful via the listing; future cards can link straight to details.
        event_url = detail_url if start and start.date() > common.TODAY.date() else listing_url
        ev = common.make_event(
            rc.clean(title.group(1)),
            rc.with_time(start, text),
            None,
            city,
            city,
            text[:500],
            event_url,
            source,
            category,
            trust,
            rc.time_text(text),
        )
        if ev:
            events.append(ev)
    return events


def _events_from_linz(html: str) -> list:
    events = []
    pat = re.compile(
        r'<a href="(?P<href>/startseite/tourismus-freizeit/veranstaltungen/events/'
        r'(?P<iso>20\d{2}-\d{2}-\d{2})-[^"]+/event\.html)">'
        r'.{0,700}?<div class="h3">\s*<a[^>]+>(?P<title>.*?)</a>',
        re.S | re.I,
    )
    for m in pat.finditer(html):
        text = rc.clean(m.group(0))
        ev = common.make_event(
            rc.clean(m.group("title")),
            common.parse_iso_date(m.group("iso")),
            None,
            "Linz am Rhein",
            "Linz am Rhein",
            text[:500],
            rc.abs_url("https://www.linz.de", m.group("href")),
            "Linz am Rhein",
            "linz mittelrhein kultur markt fest führung",
            0.84,
            rc.time_text(text),
        )
        if ev:
            events.append(ev)
    return events


def _events_from_bad_muenstereifel(html: str) -> list:
    events = []
    for block in re.findall(r'<div class="veranst_singleItem clearfix">(.*?)</div>\s*</div>\s*</div>',
                            html, re.S | re.I):
        date = re.search(r'veranst_singleItem_Headline_Dateline">(.*?)</div>', block, re.S | re.I)
        title = re.search(r'veranst_singleItem_Headline">(.*?)</div>', block, re.S | re.I)
        venue = re.search(r'veranst_singleItem_Ort">\s*(.*?)\s*</div>', block, re.S | re.I)
        href = re.search(r'<a[^>]+href="([^"]+)"', block, re.S | re.I)
        if not (date and title):
            continue
        start, end = rc.range_dates(date.group(1))
        if _is_broad_bad_muenstereifel_listing_range(start, end):
            continue
        ev = common.make_event(
            rc.clean(title.group(1)),
            start,
            end,
            rc.clean(venue.group(1) if venue else ""),
            "Bad Münstereifel",
            rc.clean(block),
            rc.abs_url("https://www.bad-muenstereifel.de", href.group(1) if href else ""),
            "Bad Münstereifel",
            "bad münstereifel kultur markt natur",
            0.78,
        )
        if ev:
            events.append(ev)
    return events


def _is_broad_bad_muenstereifel_listing_range(start, end) -> bool:
    """Suppress Deskline availability ranges that are recurring listings, not events.

    The Bad Münstereifel calendar includes rows such as ``01.01.2026 -
    31.12.2026 Montagswanderung``. Those are recurring series/listing
    availability ranges; treating them as continuous multi-day events makes
    them show up as "ongoing" in every short report window. Keep normal
    multi-day festivals while dropping broad ranges that span weeks or months.
    """
    return bool(start and end and (end - start).days > 14)


def _events_from_euskirchen(html: str) -> list:
    events = []
    for block in re.findall(r'<div class="event-list-wrapper">(.*?)</div>\s*</div>\s*</div>',
                            html, re.S | re.I):
        day = re.search(r'event-list-item-date-day">([^<]+)', block, re.S | re.I)
        month = re.search(r'event-list-item-date-month">([^<]+)', block, re.S | re.I)
        year = re.search(r'event-list-item-date-year">([^<]+)', block, re.S | re.I)
        title = re.search(r'<h3>(.*?)</h3>', block, re.S | re.I)
        venue = re.search(r'event-detail-item-date-location">(.*?)</span>', block, re.S | re.I)
        if not (day and month and year and title):
            continue
        ev = common.make_event(
            _euskirchen_title(block, title),
            rc.parse_dt(f"{rc.clean(day.group(1))} {rc.clean(month.group(1))} {rc.clean(year.group(1))}"),
            None,
            rc.clean(venue.group(1) if venue else "Stadttheater Euskirchen"),
            "Euskirchen",
            rc.clean(block),
            _euskirchen_link(block),
            "Kultur Euskirchen",
            "theater konzert kultur comedy",
            0.82,
        )
        if ev:
            events.append(ev)
    return events


def _euskirchen_title(block: str, title) -> str:
    title_text = rc.clean(title.group(1))
    subtitle = re.search(r'<h4>(.*?)</h4>', block, re.S | re.I)
    if subtitle:
        sub = rc.clean(subtitle.group(1))
        if sub and sub.lower() not in title_text.lower():
            title_text = f"{title_text}: {sub}"
    return title_text


def _euskirchen_link(block: str) -> str:
    href = re.search(r'event-list-item-text-link" href="([^"]+)"', block, re.S | re.I)
    return rc.abs_url("https://www.kultur-euskirchen.de", href.group(1) if href else "")
