"""Venue-specific calendars for the Bonn/Rhein-Sieg import proposal."""

import json
import re
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import common, http
from ..models import RawEvent
from . import regional_common as rc


def fetch() -> list:
    events = []
    events.extend(rc.fetch_html_events(
        "Rhein Sieg Forum",
        "https://www.rhein-sieg-forum.de/de/programm",
        _events_from_rhein_sieg_forum,
     source_id="rhein-sieg-forum"))
    events.extend(rc.fetch_html_events(
        "Rheinbach",
        "https://www.rheinbach.de/veranstaltungen",
        lambda html: _events_from_rheinbach(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="rheinbach", timeout=15),
        ),
     source_id="rheinbach-events"))
    events.extend(rc.fetch_html_events(
        "Arp Museum",
        "https://arpmuseum.org/veranstaltungen.html",
        _events_from_arp,
     source_id="arp-museum"))
    clickaround_url = "https://events.click-around.systems/core/19b47bb1-7fba-40a0-a4a8-8d35589b4fce/events/standard/de"
    events.extend(rc.fetch_html_events(
        "Andernach",
        clickaround_url,
        lambda html: _events_from_clickaround(html, clickaround_url),
     source_id="andernach-events"))
    events.extend(rc.fetch_html_events(
        "LVR-LandesMuseum",
        _LVR_SEARCH_URL,
        _events_from_lvr,
        fetcher=_fetch_lvr_dates,
     source_id="lvr-landesmuseum-bonn"))
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


def _events_from_rheinbach(html: str, detail_fetcher: Callable[[str], str] | None = None) -> list:
    events = []
    for block in re.findall(r'<div class="row event-item.*?(?=<div class="row event-item|<button class="event-more-button")',
                            html, re.S | re.I):
        date_text = _rheinbach_date(block)
        href = re.search(r'<a[^>]+href="([^"]*/veranstaltungen/veranstaltung/[^"]+)"', block, re.S | re.I)
        title_match = re.search(
            r'<h2[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</h2>',
            block,
            re.S | re.I,
        )
        title = rc.clean(title_match.group(1)) if title_match else rc.title_from_href(href.group(1) if href else "")
        if not (date_text and title):
            continue
        link = rc.abs_url("https://www.rheinbach.de", href.group(1) if href else "")
        venue = _rheinbach_field(block, "p", "location")
        time_text = _rheinbach_field(block, "div", "time")
        categories = _rheinbach_categories(block)
        start = rc.parse_dt(date_text)
        detail_copy = (
            _rheinbach_detail_copy(link, detail_fetcher)
            if common.window_contains(start) else ""
        )
        description = detail_copy or _rheinbach_fallback_description(
            title,
            date_text,
            time_text,
            venue,
            categories,
        )
        category_text = " ".join([*categories, "rheinbach", "lokal"])
        ev = common.make_event(
            title,
            start,
            None,
            venue,
            "Rheinbach",
            description,
            link,
            "Rheinbach",
            category_text,
            0.82,
            time_text=rc.time_text(time_text),
        )
        if ev:
            events.append(ev)
    return events


def _rheinbach_date(block: str) -> str:
    dates = [rc.clean(value) for value in re.findall(
        r'<p[^>]*class="[^"]*\bdate\b[^"]*"[^>]*>(.*?)</p>',
        block,
        re.S | re.I,
    )]
    return next((value for value in dates if re.search(r"\b20\d{2}\b", value)), "")


def _rheinbach_field(block: str, tag: str, class_name: str) -> str:
    return rc.first_group_clean(
        rf'<{tag}[^>]*class="[^"]*\b{class_name}\b[^"]*"[^>]*>(.*?)</{tag}>',
        block,
    )


def _rheinbach_categories(block: str) -> list[str]:
    section = re.search(
        r'<div[^>]*class="[^"]*\bcategories\b[^"]*"[^>]*>(.*?)</div>',
        block,
        re.S | re.I,
    )
    if not section:
        return []
    return [
        value
        for value in (rc.clean(item) for item in re.findall(r"<span[^>]*>(.*?)</span>", section.group(1), re.S | re.I))
        if value
    ]


def _rheinbach_detail_copy(link: str, detail_fetcher: Callable[[str], str] | None) -> str:
    if not (link and detail_fetcher):
        return ""
    try:
        html = detail_fetcher(link)
    except Exception as exc:
        common.log_source_error("Rheinbach detail", exc)
        return ""

    parts = []
    for class_name in ("teaser", "bodytext"):
        value = _rheinbach_field(html, "div", class_name)
        if value and value not in parts:
            parts.append(value)
    description = " ".join(parts).strip()
    if not description:
        return ""
    if not re.search(r"[.!?][\"'»)]*$", description):
        description = f"Die Stadt Rheinbach beschreibt das Programm so: {description}."
    return description


def _rheinbach_fallback_description(
    title: str,
    date_text: str,
    time_text: str,
    venue: str,
    categories: list[str],
) -> str:
    relevant_categories = [
        category
        for category in categories
        if category.casefold() not in {"allgemein", "rheinbach"}
    ]
    normalized_categories = {category.casefold() for category in relevant_categories}
    if {"sport", "aktiv"}.issubset(normalized_categories):
        introduction = f"„{title}“ ist ein Sport- und Aktivangebot in Rheinbach"
    elif relevant_categories:
        category_list = _rheinbach_join(relevant_categories)
        introduction = f"„{title}“ ist eine Veranstaltung aus den Bereichen {category_list} in Rheinbach"
    else:
        introduction = f"„{title}“ ist eine Veranstaltung in Rheinbach"

    schedule = f" am {date_text}" if date_text else ""
    times = re.findall(r"\d{1,2}:\d{2}", time_text or "")
    if len(times) >= 2:
        schedule += f" von {times[0]} bis {times[1]} Uhr"
    elif times:
        schedule += f" um {times[0]} Uhr"
    if venue:
        schedule += f" am Veranstaltungsort „{venue}“"
    return f"{introduction} und findet{schedule} statt."


def _rheinbach_join(values: list[str]) -> str:
    if len(values) < 2:
        return values[0] if values else ""
    return f"{', '.join(values[:-1])} und {values[-1]}"


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


def _clickaround_events_for_date(chunk: str, current_date: datetime, base: str) -> list:
    events = []
    for item in re.findall(r'<div class="item">(.*?)</div>\s*</div>', chunk, re.S | re.I):
        link = re.search(r'href="([^"]+)"[^>]+aria-label="Mehr Infos - ([^"]+)"', item, re.S | re.I)
        venue = re.search(
            r'<b>(?:Veranstaltungsort|Adresse):</b>\s*([^<]+)',
            item,
            re.S | re.I,
        )
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


# The site relaunched on 2026-10-07; its calendar now renders from this search index.
_LVR_SEARCH_URL = "https://www.lvr.de/landesmuseum-bonn/.elasticsearch"
_LVR_CALENDAR_URL = "https://www.lvr.de/landesmuseum-bonn/programm/terminkalender/index.html"
_LVR_SHOP_URL = "https://www.shop.landesmuseum-bonn.lvr.de/#/product/event/{event_id}?date={date}&date_id={date_id}"
_LVR_FIELDS = {
    "q": "*", "i": "gomus_landesmuseum_bonn_dates", "from": "0", "size": "500", "lang": "de",
    "sort": "timestamp", "sort_order": "asc", "filters": "{}", "send_aggregations": "false",
    "include_fields": "id,eventId,title,timestamp,description,category",
}
_BERLIN = ZoneInfo("Europe/Berlin")


def _fetch_lvr_dates(url: str, timeout: int = 25) -> str:
    return http.post_form_text(
        url, _LVR_FIELDS, timeout=timeout, headers={"Referer": _LVR_CALENDAR_URL}, retry_safe=True,
    )


def _events_from_lvr(body: str) -> list:
    return [ev for hit in json.loads(body).get("hits") or [] if (ev := _event_from_lvr_hit(hit))]


def _event_from_lvr_hit(hit: dict) -> RawEvent | None:
    title = rc.clean(str(hit.get("title") or ""))
    timestamp = hit.get("timestamp")
    if not (title and isinstance(timestamp, int | float)):
        return None
    start = datetime.fromtimestamp(timestamp / 1000, _BERLIN).replace(tzinfo=None)
    # The index stores a teaser that ends in a literal "[...]" cut marker.
    description = re.sub(r"\s*\[\.\.\.\]$", " …", rc.clean(str(hit.get("description") or "")))
    event_id, date_id = hit.get("eventId"), hit.get("id")
    link = (
        _LVR_SHOP_URL.format(event_id=event_id, date=f"{start:%Y-%m-%d}", date_id=date_id)
        if event_id and date_id else _LVR_CALENDAR_URL
    )
    category = rc.clean(str(hit.get("category") or "")).lower()
    return common.make_event(
        title,
        start,
        None,
        "LVR-LandesMuseum Bonn",
        "Bonn",
        description,
        link,
        "LVR-LandesMuseum",
        f"{category} museum ausstellung führung kino vortrag".strip(),
        0.92,
        f"{start:%H:%M}",
    )
