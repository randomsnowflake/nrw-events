"""Owning implementation of listing parsers; core is a compatibility facade."""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

from . import event_builder as _impl_event_builder
from . import text as _impl_text
from .dates import parse_date, parse_iso_date
from .location import (
    guess_city_from_text,
)


def events_from_time_listing(html: str, source: str, default_city: str, category: str,
                             trust: float, base_url: str, min_title: int = 6,
                             max_chars: int = 900, anchor_pattern: str | None = None) -> list:
    """Scrape a server-rendered listing that pairs ``<time datetime="…">`` tags with
    nearby title links — common in TYPO3 ``tx_news`` / municipal calendars that
    expose no iCal or JSON-LD feed. Each ``<time>`` is matched to the closest
    in-document anchor (within ``max_chars``) whose text looks like a real title.

    By default every ``<a>`` is a title candidate, filtered by a denylist of
    navigation labels. Pass ``anchor_pattern`` (a regex capturing href + inner
    text) to scope candidates to a CMS-specific title wrapper instead, e.g.
    ``result-list_object-title…<a href="(…)">(…)</a>``. Fails soft on unexpected
    markup (returns the events it could pair, or []).
    """
    times = [(m.start(), m.group(1)) for m in re.finditer(r'<time[^>]*datetime="([^"]+)"', html)]
    pattern = anchor_pattern or r'<a[^>]+href="([^"]+)"[^>]*>(.{0,2000}?)</a>'
    anchors = [(m.start(), m.group(1), _impl_text.clean_html(m.group(2)))
               for m in re.finditer(pattern, html, re.S | re.I)]
    # A scoped title pattern already excludes nav links; only the broad default
    # needs the denylist.
    bad = () if anchor_pattern else (
        "drucken", "session.", "weiterlesen", "mehr ", "mehr:", "details",
        "zum kalender", "veranstaltungsliste", "impressum", "anmelden", "suche")
    events, seen = [], set()
    for tp, dt in times:
        candidate = min(
            (
                (abs(ap - tp), href, title)
                for ap, href, title in anchors
                if abs(ap - tp) < max_chars and len(title) >= min_title
                and not any(marker in title.lower() for marker in bad)
            ),
            default=None,
        )
        if candidate is None:
            continue
        _, href, title = candidate
        key = (title.lower(), dt[:10])
        if key in seen:
            continue
        seen.add(key)
        start = parse_iso_date(dt)
        link = urllib.parse.urljoin(base_url, href)
        ev = _impl_event_builder.make_event(title, start, None, "", default_city, "", link, source, category, trust)
        if ev:
            events.append(ev)
    return events


def events_from_ecmaps_tiles(html: str, source: str, default_city: str, category: str,
                             trust: float, base_url: str) -> list:
    """Parse destination.one / ECMaps tile listings with date, title, and venue.

    Used by regional tourism calendars such as Naturregion Sieg. The markup is
    server-rendered but minified, so this intentionally pairs fields inside the
    tile anchor instead of relying on line structure.
    """
    events, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(?P<href>[^"]+)"[^>]*class="[^"]*tile__link[^"]*"[^>]*>(?P<body>.{0,2000}?)</a>',
                         html, re.S | re.I):
        href = m.group("href")
        body = m.group("body")
        if "${" in href or "${" in body:
            continue
        date_m = re.search(r'tile__label-text[^>]*>\s*(.*?)\s*</span>', body, re.S | re.I)
        title_m = re.search(r'header__head[^>]*>\s*(.*?)\s*</p>', body, re.S | re.I)
        venue_m = re.search(r'icontext__text[^>]*>\s*(.*?)\s*</span>', body, re.S | re.I)
        if not (date_m and title_m):
            continue
        title = _impl_text.clean_html(title_m.group(1))
        date_text = _impl_text.clean_html(date_m.group(1))
        venue = _impl_text.clean_html(venue_m.group(1) if venue_m else "")
        start = parse_date(date_text)
        city = guess_city_from_text(venue) or default_city
        key = (title.lower(), start.strftime("%Y-%m-%d") if start else date_text)
        if key in seen:
            continue
        seen.add(key)
        ev = _impl_event_builder.make_event(
            title, start, start, venue, city, "", urllib.parse.urljoin(base_url, href),
            source, category, trust,
        )
        if ev:
            events.append(ev)
    return events


def _wp_event_manager_datetimes(text: str) -> tuple:
    text = re.sub(r"\s+", " ", _impl_text.clean_html(text))
    m = re.search(
        r"(?P<start>\d{1,2}\.\d{1,2}\.20\d{2})(?:\s*@\s*(?P<stime>\d{1,2}:\d{2}))?"
        r"(?:\s*-\s*(?P<end>\d{1,2}\.\d{1,2}\.20\d{2})?\s*@?\s*(?P<etime>\d{1,2}:\d{2})?)?",
        text,
    )
    if not m:
        return None, None, ""
    start = parse_date(m.group("start"))
    end = parse_date(m.group("end") or m.group("start"))
    stime, etime = m.group("stime"), m.group("etime")
    if start and stime:
        hour, minute = map(int, stime.split(":"))
        start = start.replace(hour=hour, minute=minute)
    if end and etime:
        hour, minute = map(int, etime.split(":"))
        end = end.replace(hour=hour, minute=minute)
    time_text = f"{stime}-{etime}" if stime and etime else (stime or "")
    return start, end, time_text


def events_from_wp_event_manager_listing(html: str, source: str, category: str, trust: float) -> list:
    """Parse WP Event Manager list cards, skipping locations outside known towns."""
    events, seen = [], set()
    for m in re.finditer(r'<div class="event_listing\b(?P<body>.{0,2000}?)</a>', html, re.S | re.I):
        body = m.group("body")
        href_m = re.search(r'<a[^>]+href="([^"]+)"', body, re.S | re.I)
        title_m = re.search(r'wpem-event-title.*?<h3[^>]*>(.*?)</h3>', body, re.S | re.I)
        date_m = re.search(r'wpem-event-date-time.*?<span[^>]*>(.*?)</span>', body, re.S | re.I)
        loc_m = re.search(r'wpem-event-location.*?<span[^>]*>(.*?)</span>', body, re.S | re.I)
        if not (href_m and title_m and date_m and loc_m):
            continue
        title = _impl_text.clean_html(title_m.group(1))
        location = _impl_text.clean_html(loc_m.group(1))
        city = guess_city_from_text(location)
        if not city:
            continue
        start, end, time_text = _wp_event_manager_datetimes(date_m.group(1))
        key = (title.lower(), start.strftime("%Y-%m-%d") if start else "")
        if key in seen:
            continue
        seen.add(key)
        ev = _impl_event_builder.make_event(
            title, start, end, location, city, "", href_m.group(1),
            source, category, trust, time_text=time_text,
        )
        if ev:
            events.append(ev)
    return events
