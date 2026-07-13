"""Nearby tourism and destination calendars around Bonn."""

import re

from .. import common
from . import regional_common as rc

_LINZ_URL = "https://www.linz.de/startseite/tourismus-freizeit/veranstaltungen"


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
    events.extend(_fetch_linz())
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
        location = re.search(
            r'shapehub-location-line.*?<span>(.*?)</span>', body, re.S | re.I,
        )
        city = rc.clean(location.group(1)) if location else rc.city_from_text(text, default_city)
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


def _fetch_linz() -> list:
    events = []
    url = _LINZ_URL
    seen_urls = set()
    try:
        for _ in range(6):
            if not url or url in seen_urls:
                break
            seen_urls.add(url)
            html = common.fetch_url(url, timeout=25)
            events.extend(_events_from_linz(
                html,
                detail_fetcher=lambda detail_url: common.fetch_detail_url(
                    detail_url, cache_namespace="linz-am-rhein", timeout=20),
            ))
            dates = [
                common.parse_iso_date(value)
                for value in re.findall(r'/events/(20\d{2}-\d{2}-\d{2})-', html, re.I)
            ]
            if dates and max(date for date in dates if date) >= common.END_DATE:
                break
            next_page = re.search(
                r'<li class="next"><a[^>]+href="([^"]+)"', html, re.S | re.I)
            url = rc.abs_url(_LINZ_URL, next_page.group(1)) if next_page else ""
    except Exception as exc:
        common.log_source_error("Linz am Rhein", exc)
    return events


def _linz_detail_context(html: str) -> dict:
    description = re.search(
        r'<h1>.*?</h1>\s*<span class="centered">(.*?)</span>', html or "", re.S | re.I)
    venue = re.search(
        r'<i class="[^"]*\bicon-pin\b[^"]*"></i>\s*([^<]+)', html or "", re.S | re.I)
    event_time = re.search(
        r'class="[^"]*\bevent-time\b[^"]*".*?(\d{1,2}:\d{2})', html or "", re.S | re.I)
    return {
        "description": common.concise_description(
            rc.clean(description.group(1) if description else "")),
        "venue": common.normalize_venue_name(venue.group(1) if venue else ""),
        "time": event_time.group(1) if event_time else "",
    }


def _linz_fallback_description(title: str, date: str, time_text: str,
                               venue: str, listing_copy: str) -> str:
    if listing_copy and len(listing_copy) >= 80:
        return common.concise_description(listing_copy)
    schedule = f" am {date}" if date else ""
    if time_text:
        schedule += f" um {time_text} Uhr"
    if venue:
        schedule += f" am Veranstaltungsort „{venue}“"
    extra = f" {listing_copy}." if listing_copy else ""
    return f"„{title}“ ist im Linzer Veranstaltungskalender{schedule} angekündigt.{extra}".strip()


def _events_from_linz(html: str, detail_fetcher=None) -> list:
    events = []
    for block in re.split(r'(?=<div class="standardteaser">)', html or "", flags=re.I):
        if not block.lstrip().startswith('<div class="standardteaser">'):
            continue
        href = re.search(
            r'href="(?P<href>/startseite/tourismus-freizeit/veranstaltungen/events/'
            r'(?P<iso>20\d{2}-\d{2}-\d{2})-[^"]+/event\.html)"', block, re.S | re.I)
        title = re.search(
            r'<div class="h3">\s*<a[^>]*>(.*?)</a>', block, re.S | re.I)
        if not (href and title):
            continue
        link = rc.abs_url("https://www.linz.de", href.group("href"))
        listing_copy_match = re.search(
            r'<div class="teasertext">(.*?)</div>', block, re.S | re.I)
        listing_copy = rc.clean(listing_copy_match.group(1) if listing_copy_match else "")
        context = {}
        if detail_fetcher:
            try:
                context = _linz_detail_context(detail_fetcher(link))
            except Exception as exc:
                common.log_source_error("Linz am Rhein detail", exc)
        start = common.parse_iso_date(href.group("iso"))
        time_text = context.get("time") or rc.time_text(block)
        start = rc.with_time(start, time_text)
        title_text = rc.clean(title.group(1))
        venue = context.get("venue") or "Linz am Rhein"
        description = context.get("description") or _linz_fallback_description(
            title_text, href.group("iso"), time_text, venue, listing_copy)
        ev = common.make_event(
            title_text,
            start,
            None,
            venue,
            "Linz am Rhein",
            description,
            link,
            "Linz am Rhein",
            "linz mittelrhein kultur markt fest führung",
            0.84,
            time_text,
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
