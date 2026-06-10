"""Venue-specific calendars for the Bonn/Rhein-Sieg import proposal."""

import re
from html import unescape

from .. import common
from . import regional_common as rc


def fetch() -> list:
    events = []
    events.extend(rc.fetch_html_events(
        "Rhein Sieg Forum",
        "https://www.rhein-sieg-forum.de/de/programm",
        _events_from_rhein_sieg_forum,
    ))
    events.extend(rc.fetch_html_events(
        "Rheinbach",
        "https://www.rheinbach.de/veranstaltungen",
        _events_from_rheinbach,
    ))
    events.extend(rc.fetch_html_events(
        "Arp Museum",
        "https://arpmuseum.org/veranstaltungen.html",
        _events_from_arp,
    ))
    clickaround_url = "https://events.click-around.systems/core/19b47bb1-7fba-40a0-a4a8-8d35589b4fce/events/standard/de"
    events.extend(rc.fetch_html_events(
        "Andernach",
        clickaround_url,
        lambda html: _events_from_clickaround(html, clickaround_url),
    ))
    events.extend(rc.fetch_html_events(
        "LVR-LandesMuseum",
        "https://landesmuseum-bonn.lvr.de/de/veranstaltungen/veranstaltungen_2/alleveranstaltungen.html",
        _events_from_lvr,
    ))
    return rc.dedupe(events)


def _events_from_rhein_sieg_forum(html: str) -> list:
    events = []
    for block in re.split(r'(?=<a class="listteaser-link")', html):
        if 'class="listteaser-link"' not in block:
            continue
        href = re.search(r'href="([^"]+)"', block, re.I)
        date = re.search(r'(\d{1,2}\.\s*[A-Za-zäöüÄÖÜ]+\s*20\d{2})', rc.clean(block), re.I)
        title_text = _rhein_sieg_forum_title(block, href.group(1) if href else "")
        if not (date and title_text):
            continue
        ev = common.make_event(
            title_text,
            rc.parse_dt(date.group(1)),
            None,
            "Rhein Sieg Forum",
            "Siegburg",
            rc.clean(block),
            rc.abs_url("https://www.rhein-sieg-forum.de", href.group(1) if href else ""),
            "Rhein Sieg Forum",
            "show comedy konzert messe kultur",
            0.9,
        )
        if ev:
            events.append(ev)
    return events


def _rhein_sieg_forum_title(block: str, href: str) -> str:
    title = re.search(r'<h2[^>]*class="h200"[^>]*>(.*?)</h2>', block, re.S | re.I)
    title_text = rc.clean(title.group(1)) if title else ""
    if title_text and not re.match(r"^\d", title_text):
        return title_text
    alt = re.search(r'<img[^>]+alt="([^"]+)"', block, re.S | re.I)
    return rc.clean(alt.group(1)) if alt else rc.title_from_href(href)


def _events_from_rheinbach(html: str) -> list:
    events = []
    for block in re.findall(r'<div class="row event-item.*?(?=<div class="row event-item|<button class="event-more-button")',
                            html, re.S | re.I):
        date = re.search(r'<p class="date">\s*([^<]+)', block, re.S | re.I)
        href = re.search(r'<a[^>]+href="([^"]*/veranstaltungen/veranstaltung/[^"]+)"', block, re.S | re.I)
        teaser = re.search(r'<p class="teaser">(.*?)</p>', block, re.S | re.I)
        title = rc.clean(teaser.group(1)) if teaser else rc.title_from_href(href.group(1) if href else "")
        if not (date and title):
            continue
        ev = common.make_event(
            title,
            rc.parse_dt(date.group(1)),
            None,
            "",
            "Rheinbach",
            rc.clean(block),
            rc.abs_url("https://www.rheinbach.de", href.group(1) if href else ""),
            "Rheinbach",
            "rheinbach lokal kultur markt",
            0.82,
        )
        if ev:
            events.append(ev)
    return events


def _events_from_arp(html: str) -> list:
    events = []
    blocks = re.findall(
        r'<a href="([^"]*/veranstaltungen/detail/[^"]+)">(.*?)(?=<a href="[^"]*/veranstaltungen/detail/|</ul>|</section>)',
        html,
        re.S | re.I,
    )
    for href, body in blocks:
        date = re.search(r'va-date-block"><span>(\d{1,2})\s+([A-Za-z]+)</span>\s*(20\d{2})', body, re.S | re.I)
        title = re.search(r'<h3 class="va-title">(.*?)</h3>', body, re.S | re.I)
        typ = re.search(r'<p class="va-type">(.*?)</p>', body, re.S | re.I)
        if not (date and title):
            continue
        ev = common.make_event(
            rc.clean(title.group(1)),
            rc.parse_dt(f"{date.group(1)} {date.group(2)} {date.group(3)}"),
            None,
            "Arp Museum Bahnhof Rolandseck",
            "Remagen",
            rc.clean(typ.group(1) if typ else ""),
            rc.abs_url("https://arpmuseum.org", href),
            "Arp Museum",
            "museum ausstellung führung workshop kultur",
            0.9,
        )
        if ev:
            events.append(ev)
    return events


def _events_from_clickaround(html: str, base: str) -> list:
    events, current_date = [], None
    chunks = re.split(r'(<div class="ui dividing header">[^<]+</div>)', html)
    for chunk in chunks:
        header = re.search(r'ui dividing header">\s*([^<]+)', chunk)
        if header:
            current_date = rc.parse_dt(header.group(1))
            continue
        if not current_date:
            continue
        events.extend(_clickaround_events_for_date(chunk, current_date, base))
    return events


def _clickaround_events_for_date(chunk: str, current_date, base: str) -> list:
    events = []
    for item in re.findall(r'<div class="item">(.*?)</div>\s*</div>', chunk, re.S | re.I):
        link = re.search(r'href="([^"]+)"[^>]+aria-label="Mehr Infos - ([^"]+)"', item, re.S | re.I)
        venue = re.search(r'<b>Veranstaltungsort:</b>\s*([^<]+)', item, re.S | re.I)
        if not link:
            continue
        ev = common.make_event(
            rc.clean(link.group(2)),
            current_date,
            None,
            rc.clean(venue.group(1) if venue else ""),
            "Andernach",
            rc.clean(item),
            rc.abs_url(base, link.group(1)),
            "Andernach",
            "andernach kultur konzert theater open air fest",
            0.84,
        )
        if ev:
            events.append(ev)
    return events


def _events_from_lvr(html: str) -> list:
    events = []
    for body in re.split(r'(?=<div class="event filter-element)', html):
        if 'data-filter-list=' not in body:
            continue
        ev = _event_from_lvr_body(body)
        if ev:
            events.append(ev)
    return events


def _event_from_lvr_body(body: str):
    text = rc.clean(body)
    href = re.search(r'<a class="more"[^>]+href="([^"]+)"', body, re.S | re.I)
    data = re.search(r'data-filter-list="([^"]+)"', body, re.S | re.I)
    hay = rc.clean(data.group(1) if data else text)
    parts = [part.strip() for part in hay.split(",")]
    title = parts[1] if len(parts) > 1 else ""
    date = re.search(r"(\d{1,2}\.\d{1,2}\.)\s*(\d{1,2}:\d{2})", hay)
    if not (title and date):
        return None
    dt = common.parse_date(f"{date.group(1)}{common.TODAY.year}")
    return common.make_event(
        title,
        rc.with_time(dt, date.group(2)),
        None,
        "LVR-LandesMuseum Bonn",
        "Bonn",
        text[:500],
        unescape(href.group(1)).strip() if href else "",
        "LVR-LandesMuseum",
        "museum ausstellung führung kino vortrag",
        0.92,
        date.group(2),
    )
