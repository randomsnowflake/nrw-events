"""Requested Bonn/Rhein-Sieg venue and municipal calendars."""

import re
from datetime import datetime

from .. import common
from . import regional_common as rc


def fetch() -> list:
    events = []
    events.extend(rc.fetch_html_events(
        "Kunstmuseum Bonn",
        "https://www.kunstmuseum-bonn.de/de/besuch/kalender/",
        lambda html: _events_from_kunstmuseum_bonn(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="kunstmuseum-bonn", timeout=20),
        ),
    ))
    events.extend(rc.fetch_html_events(
        "Sankt Augustin",
        "https://www.sankt-augustin.de/veranstaltungen/",
        _events_from_sankt_augustin,
    ))
    events.extend(rc.fetch_html_events(
        "Pantheon Bonn",
        "https://www.pantheon.de/programm/",
        _events_from_pantheon,
    ))
    events.extend(rc.fetch_html_events(
        "Haus der Springmaus",
        "https://www.springmaus-theater.de/events.html?s=all",
        _events_from_springmaus,
    ))
    events.extend(rc.fetch_html_events(
        "Brückenforum Bonn",
        "https://www.brueckenforum.de/alle-events/",
        lambda html: _events_from_brueckenforum(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="brueckenforum-bonn", timeout=20),
        ),
    ))
    return rc.dedupe(events)


def _kunstmuseum_detail_description(html: str) -> str:
    body = re.search(
        r'<div[^>]*class="[^"]*\bpost-body\b[^"]*"[^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(
        rc.clean(body.group(1) if body else ""), max_chars=360)


def _kunstmuseum_fallback_description(title: str, format_text: str, start) -> str:
    schedule = f" am {start:%d.%m.%Y}" if start else ""
    if start and start.strftime("%H:%M") != "00:00":
        schedule += f" um {start:%H:%M} Uhr"
    format_label = format_text or "Veranstaltung"
    return f"„{title}“ ist ein Angebot im Format „{format_label}“ und findet{schedule} im Kunstmuseum Bonn statt."


def _events_from_kunstmuseum_bonn(html: str, detail_fetcher=None) -> list:
    events = []
    for block in re.findall(r'<a href="(?P<href>[^"]+/de/besuch/kalender/[^"]+/)">(.*?)</a>',
                            html, re.S | re.I):
        href, body = block
        date_m = re.search(r'class="teaser-date">\s*(.*?)\s*</p>', body, re.S | re.I)
        title_m = re.search(r'class="teaser-title">\s*(.*?)\s*</h4>', body, re.S | re.I)
        meta_m = re.search(r'class="teaser-meta">\s*(.*?)\s*</p>', body, re.S | re.I)
        if not (date_m and title_m):
            continue
        date_text = rc.clean(date_m.group(1))
        start = rc.with_time(common.parse_date(date_text), date_text)
        title = rc.clean(title_m.group(1))
        format_text = rc.clean(meta_m.group(1) if meta_m else "")
        fallback = _kunstmuseum_fallback_description(title, format_text, start)
        description = ""
        if detail_fetcher and common.window_contains(start):
            try:
                description = _kunstmuseum_detail_description(detail_fetcher(href))
            except Exception as exc:
                common.log_source_error("Kunstmuseum Bonn detail", exc)
        ev = common.make_event(
            title,
            start,
            start,
            "Kunstmuseum Bonn",
            "Bonn",
            description or fallback,
            href,
            "Kunstmuseum Bonn",
            "museum kunst ausstellung führung workshop performance lesung konzert",
            0.92,
        )
        if ev:
            events.append(ev)
    return events


def _events_from_sankt_augustin(html: str) -> list:
    events = []
    for article in re.findall(r'<article class="[^"]*mec-event-article[^"]*".*?</article>',
                              html, re.S | re.I):
        title_m = re.search(r'<h3 class="mec-event-title">.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                            article, re.S | re.I)
        desc_m = re.search(r'<div class="mec-event-description">(.*?)</div>', article, re.S | re.I)
        date_m = re.search(r'occurrence=(\d{4}-\d{2}-\d{2})', article, re.S | re.I)
        label_m = re.search(r'class="mec-start-date-label"[^>]*>(.*?)</span>', article, re.S | re.I)
        start_m = re.search(r'class="mec-start-time">\s*(\d{1,2}:\d{2})\s*</span>', article, re.S | re.I)
        end_m = re.search(r'class="mec-end-time">\s*(\d{1,2}:\d{2})\s*</span>', article, re.S | re.I)
        venue_m = re.search(r'class="mec-venue-details">\s*<span>(.*?)</span>', article, re.S | re.I)
        address_m = re.search(r'class="mec-event-address">\s*<span>(.*?)</span>', article, re.S | re.I)
        if not title_m:
            continue
        start = _date_from_occurrence_or_label(date_m.group(1) if date_m else "", label_m.group(1) if label_m else "")
        start = _with_hhmm(start, start_m.group(1) if start_m else "")
        end = _with_hhmm(_date_from_occurrence_or_label(date_m.group(1) if date_m else "", label_m.group(1) if label_m else ""),
                         end_m.group(1) if end_m else "")
        venue = rc.clean(" ".join(x for x in [
            venue_m.group(1) if venue_m else "",
            address_m.group(1) if address_m else "",
        ] if x))
        ev = common.make_event(
            rc.clean(title_m.group(2)),
            start,
            end or start,
            venue,
            "Sankt Augustin",
            rc.clean(desc_m.group(1) if desc_m else ""),
            title_m.group(1),
            "Sankt Augustin",
            "sankt augustin lokal kultur markt fest sport natur",
            0.84,
        )
        if ev:
            events.append(ev)
    return events


def _events_from_pantheon(html: str) -> list:
    events = []
    current_year = _first_program_year(html, common.TODAY.year)
    last_month = 0
    for item in re.findall(r'<li id="t(?P<id>\d+)">(.*?)</li>', html, re.S | re.I):
        event_id, body = item
        if "event-reschedule" in body and re.search(r"neuer\s+termin|verschoben", body, re.I):
            continue
        day_m = re.search(r'class="event-date-1">\s*(\d{1,2})\.', body, re.S | re.I)
        month_m = re.search(r'class="event-date-3">\s*([A-Za-zäöüÄÖÜ]+)\s*</div>', body, re.S | re.I)
        time_m = re.search(r'class="event-time">\s*(\d{1,2}:\d{2})\s*</div>', body, re.S | re.I)
        title_m = re.search(r'class="event-title">\s*(.*?)\s*</h2>', body, re.S | re.I)
        venue_m = re.search(r'class="event-location[^"]*"[^>]*>\s*<a[^>]*>\s*(.*?)\s*</a>', body, re.S | re.I)
        types = " ".join(rc.clean(x) for x in re.findall(r'class="event-type">\s*(.*?)\s*</h3>', body, re.S | re.I))
        if not (day_m and month_m and title_m):
            continue
        month = common.MONTH_DE.get(month_m.group(1).lower())
        if not month:
            continue
        if last_month and month < last_month:
            current_year += 1
        last_month = month
        start = datetime(current_year, month, int(day_m.group(1)))
        start = _with_hhmm(start, time_m.group(1) if time_m else "")
        link = f"https://www.pantheon.de/programm/#t{event_id}"
        ev = common.make_event(
            rc.clean(title_m.group(1)).strip(" -"),
            start,
            start,
            rc.clean(venue_m.group(1) if venue_m else "Pantheon"),
            "Bonn",
            types,
            link,
            "Pantheon Bonn",
            "theater kabarett comedy konzert lesung poetry slam kultur",
            0.92,
        )
        if ev:
            events.append(ev)
    return events


def _events_from_springmaus(html: str) -> list:
    events = []
    for block in re.findall(r'<div class="[^"]*bg-\[#aba199\][\s\S]*?(?=<div class="[^"]*bg-\[#aba199\]|</main>|$)',
                            html, re.I):
        date_link = re.search(r'<a class="flex[^"]*" href="([^"]+)">(.*?)</a>', block, re.S | re.I)
        title_m = re.search(r'<h3[^>]*>\s*<a href="([^"]+)">(.*?)</a>\s*</h3>', block, re.S | re.I)
        price_m = re.search(r'<div class="leading-tight">\s*(.*?)\s*</div>', block, re.S | re.I)
        if not (date_link and title_m):
            continue
        date_text = rc.clean(date_link.group(2))
        start = rc.with_time(_parse_springmaus_dt(date_text), date_text)
        if not start:
            continue
        link = rc.abs_url("https://www.springmaus-theater.de/", title_m.group(1) or date_link.group(1))
        ev = common.make_event(
            rc.clean(title_m.group(2)),
            start,
            start,
            "Haus der Springmaus",
            "Bonn",
            rc.clean(price_m.group(1) if price_m else ""),
            link,
            "Haus der Springmaus",
            "theater comedy kabarett impro konzert kultur",
            0.92,
        )
        if ev:
            events.append(ev)
    return events


def _brueckenforum_detail_context(html: str) -> dict:
    event_section = re.search(
        r'<section[^>]+id="single-event-header"[^>]*>(.*?)</section>',
        html or "",
        re.S | re.I,
    )
    body = event_section.group(1) if event_section else (html or "")
    text = rc.clean(body)
    time_match = re.search(
        r"(?:Einlass|Zeitraum)[^0-9]{0,30}(?:Immer\s+)?von\s*"
        r"(\d{1,2})(?::(\d{2}))?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*Uhr",
        text,
        re.I,
    )
    if not time_match:
        time_match = re.search(
            r"Einlass:\s*(\d{1,2}):(\d{2})(?::\d{2})?",
            text,
            re.I,
        )
    time_text = ""
    if time_match and len(time_match.groups()) == 4:
        start_hour, start_minute, end_hour, end_minute = time_match.groups()
        time_text = (
            f"{int(start_hour):02d}:{start_minute or '00'}–"
            f"{int(end_hour):02d}:{end_minute or '00'}"
        )
    elif time_match:
        start_hour, start_minute = time_match.groups()
        time_text = f"{int(start_hour):02d}:{start_minute}"

    visitor_free = bool(re.search(
        r"Eintritt\s+für\s+Besucher\s*:\s*Kostenlos", text, re.I))
    price_match = re.search(r"\bEintritt\s+(\d+(?:[,.]\d+)?)\s*€", text, re.I)
    price = "kostenlos" if visitor_free else (
        f"{price_match.group(1).replace(',', '.')} €" if price_match else ""
    )
    is_rathaus_market = bool(re.search(
        r"(?:Floh|Trödelmarkt).*Rathausplatz|Beueler\s+Rathausplatz",
        text,
        re.I,
    ))
    description_candidates = []
    for value in re.findall(r"<div\b[^>]*>(.*?)</div>", body, re.S | re.I):
        cleaned = rc.clean(value)
        if (
            len(cleaned) >= 20
            and not re.search(r"\b(?:Eintritt|Tickets|Einlass|Zeitraum)\b", cleaned, re.I)
            and not re.fullmatch(r"\d{1,2}/\d{1,2}/20\d{2}.*", cleaned)
        ):
            description_candidates.append(cleaned)
    description = common.concise_description(
        max(description_candidates, key=len) if description_candidates else "",
        max_chars=360,
    )
    return {
        "time": time_text,
        "price": price,
        "description": description,
        "is_rathaus_market": is_rathaus_market,
    }


def _events_from_brueckenforum(html: str, detail_fetcher=None) -> list:
    events = []
    for block in re.findall(r'<div class="event-single">(.*?)</div>\s*</div>\s*</div>',
                            html, re.S | re.I):
        category_m = re.search(r'class="event-headline[^"]*"[^>]*>(.*?)</h3>', block, re.S | re.I)
        title_m = re.search(r'<h4>\s*(.*?)\s*</h4>', block, re.S | re.I)
        date_m = re.search(r'<span class="date">\s*(\d{1,2}/\d{1,2}/20\d{2})\s*</span>', block, re.S | re.I)
        href_m = re.search(r'<a href="([^"]+/events/[^"]+/)"', block, re.S | re.I)
        if not (category_m and title_m and date_m):
            continue
        category = rc.clean(category_m.group(1))
        if re.search(r"\babi|abiball|abschlussball", category, re.I):
            continue
        start = _parse_slash_date(date_m.group(1))
        link = href_m.group(1) if href_m else "https://www.brueckenforum.de/alle-events/"
        detail = {}
        is_market = bool(re.search(
            r"floh|trödel|troedel",
            rc.clean(title_m.group(1)),
            re.I,
        ))
        if detail_fetcher and is_market and common.window_contains(start):
            try:
                detail = _brueckenforum_detail_context(detail_fetcher(link))
            except Exception as exc:
                common.log_source_error("Brückenforum Bonn detail", exc)
        title = rc.clean(title_m.group(1))
        venue = "Brückenforum Bonn"
        if detail.get("is_rathaus_market"):
            title = "Floh- und Trödelmarkt Beueler Rathausplatz"
            venue = "Beueler Rathausplatz (Möhneplatz)"
        start_with_time = _with_hhmm(start, detail.get("time", ""))
        end_with_time = start_with_time
        if "–" in detail.get("time", ""):
            end_with_time = _with_hhmm(start, detail["time"].split("–", 1)[1])
        ev = common.make_event(
            title,
            start_with_time,
            end_with_time,
            venue,
            "Bonn",
            detail.get("description") or category,
            link,
            "Brückenforum Bonn",
            f"brückenforum {category} konzert comedy theater show markt party",
            0.98 if detail else 0.9,
            detail.get("time", ""),
            source_id="brueckenforum-bonn",
        )
        if ev:
            if detail.get("price"):
                ev["price"] = detail["price"]
            events.append(ev)
    return events


def _with_hhmm(dt, time_text: str):
    if not dt or not time_text:
        return dt
    m = re.search(r"(\d{1,2}):(\d{2})", time_text)
    return dt.replace(hour=int(m.group(1)), minute=int(m.group(2))) if m else dt


def _date_from_occurrence_or_label(occurrence: str, label: str):
    if occurrence:
        return common.parse_iso_date(occurrence)
    parts = rc.clean(label).split()
    if len(parts) >= 2:
        month = common.MONTH_DE.get(parts[1].lower().rstrip("."))
        if month:
            return rc.date_for_window(int(parts[0]), month)
    return None


def _parse_slash_date(text: str):
    try:
        return datetime.strptime(text, "%d/%m/%Y")
    except ValueError:
        return None


def _parse_springmaus_dt(text: str):
    normalized = re.sub(r"\b([A-Za-zäöüÄÖÜ]{3,})\.", r"\1", text or "")
    return rc.parse_dt(normalized)


def _first_program_year(html: str, fallback: int) -> int:
    m = re.search(r'/programm/\?date=(20\d{2})-\d{2}', html)
    return int(m.group(1)) if m else fallback
