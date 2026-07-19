"""Curated cinema events that are meaningfully different from normal screenings.

Every broad cinema source in this module has an explicit special-format gate.
Dedicated festival and student-cinema programs are special by definition; cinema
chains and mixed calendars must expose a label or tag such as ``Open Air``,
``Preview`` or ``Filmgespräch``.  Ordinary repertory and first-run showtimes are
intentionally not imported.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import common
from . import regional_common as rc


_BONNER_KINEMATHEK_URL = "https://www.bonnerkinemathek.de/programm/"
_CAMPUS_KINO_URL = "https://www.unifilm.de/studentenkinos/Bonn"
_STUMMFILMTAGE_URL = "https://www.internationale-stummfilmtage.de/"
_KINOPOLIS_URL = "https://www.kinopolis.de/bn/events"
_FILMHAUS_API = "https://backend.filmhaus-koeln.de/events"
_KURZFILMWANDERUNG_URL = "https://kurzfilmwanderung-bonn.de/"
_KULTURBAD_ICAL = "https://ruengsdorfer-kulturbad.de/events/?ical=1"

_BONNER_KINEMATHEK_SOURCE_ID = "bonner-kinemathek"
_CAMPUS_KINO_SOURCE_ID = "campus-kino-bonn"
_STUMMFILMTAGE_SOURCE_ID = "internationale-stummfilmtage"
_KINOPOLIS_SOURCE_ID = "kinopolis-bonn"
_FILMHAUS_SOURCE_ID = "filmhaus-koeln"
_KURZFILMWANDERUNG_SOURCE_ID = "kurzfilmwanderung-bonn"
_KULTURBAD_SOURCE_ID = "ruengsdorfer-kulturbad"

_SPECIAL_FORMAT_PATTERN = re.compile(
    r"\b(?:film(?:tage|tag|fest(?:ival)?|nacht|nächte|naechte|gespräch|gespraech)|"
    r"festival|open[ -]?air|freiluft(?:kino)?|preview|vorpremiere|premiere|"
    r"sondervorstellung|retrospektive|filmquiz|q\s*&\s*a|diskussion|"
    r"cineville|short monday|anniversary|jubiläum|jubilaeum|live[- ]?musik)\b",
    re.I,
)
_FILMHAUS_SPECIAL_TAG_PATTERN = re.compile(
    r"\b(?:open air|preview|premiere|sneak peek|workshop|film auf film|"
    r"filmbildung|filmquiz|interaktives quiz|short monday|nrw on fire|"
    r"familiensonntag|anniversary|jubiläum|jubilaeum|festival|filmgespräch|"
    r"filmgespraech|diskussion|sonderveranstaltung)\b",
    re.I,
)
_KULTURBAD_CINEMA_PATTERN = re.compile(
    r"\b(?:open[ -]?air[ -]?kino|freiluftkino|film(?:nacht|nächte|naechte|festival|fest|tage))\b",
    re.I,
)


def fetch() -> list:
    """Fetch all curated cinema-special sources, isolating source failures."""
    events = []
    # Special formats are optional on these broad cinema calendars. An empty
    # filtered result is healthy and must not be reported as parser drift.
    events.extend(_fetch_optional_html(
        "Bonner Kinemathek",
        _BONNER_KINEMATHEK_SOURCE_ID,
        _BONNER_KINEMATHEK_URL,
        lambda html: _events_from_bonner_kinemathek(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="bonner-kinemathek-specials", timeout=20),
        ),
    ))
    events.extend(rc.fetch_html_events(
        "Campus-Kino Bonn",
        _CAMPUS_KINO_URL,
        _events_from_campus_kino,
        source_id=_CAMPUS_KINO_SOURCE_ID,
    ))
    events.extend(rc.fetch_html_events(
        "Internationale Stummfilmtage",
        _STUMMFILMTAGE_URL,
        _events_from_stummfilmtage,
        source_id=_STUMMFILMTAGE_SOURCE_ID,
    ))
    events.extend(_fetch_optional_html(
        "KINOPOLIS Bonn",
        _KINOPOLIS_SOURCE_ID,
        _KINOPOLIS_URL,
        lambda html: _events_from_kinopolis_listing(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="kinopolis-bonn-specials", timeout=20),
        ),
    ))
    events.extend(_fetch_filmhaus_events())
    events.extend(rc.fetch_html_events(
        "Kurzfilmwanderung Bonn",
        _KURZFILMWANDERUNG_URL,
        _events_from_kurzfilmwanderung,
        source_id=_KURZFILMWANDERUNG_SOURCE_ID,
    ))
    events.extend(_fetch_kulturbad_cinema())
    return rc.dedupe(events)


def _fetch_optional_html(name: str, source_id: str, url: str, parser) -> list:
    try:
        return parser(common.fetch_url(url, timeout=25))
    except Exception as exc:
        common.log_source_error(name, exc, source_id=source_id)
        return []


def _events_from_bonner_kinemathek(html: str, detail_fetcher=None) -> list:
    events = []
    blocks = re.findall(
        r'(<div class="em-event\s+em-item\s+em-list-item".*?)(?=<div class="em-event\s+em-item\s+em-list-item"|<h3 class="grplst"|$)',
        html or "",
        re.S | re.I,
    )
    for body in blocks:
        href_m = re.search(r'data-href="([^"]+)"', body, re.I)
        title_m = re.search(r'class="[^"]*em-item-title[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>', body, re.S | re.I)
        date_m = re.search(r'class="[^"]*em-event-date[^"]*"[^>]*>(.*?)</div>', body, re.S | re.I)
        if not (href_m and title_m and date_m):
            continue

        title = rc.clean(title_m.group(1))
        tag_lines = re.findall(
            r'class="[^"]*em-icon-tag[^"]*".*?</span>\s*(.*?)</div>',
            body,
            re.S | re.I,
        )
        tags = rc.clean(" ".join(tag_lines))
        if not _is_special_format(title, tags):
            continue
        if re.search(r"\bkeine\s+(?:veranstaltung|vorstellung|filmvorführung)", title, re.I):
            continue

        date_text = rc.clean(date_m.group(1))
        time_m = re.search(
            r'class="[^"]*em-event-time[^"]*"[^>]*>\s*<span[^>]*em-icon-clock[^>]*>.*?</span>\s*(\d{1,2}:\d{2})',
            body,
            re.S | re.I,
        )
        start = rc.with_time(common.parse_date(date_text), time_m.group(1) if time_m else "")
        venue_m = re.search(r'class="[^"]*em-event-location[^"]*"[^>]*>(.*?)</div>', body, re.S | re.I)
        venue = rc.clean(venue_m.group(1) if venue_m else "Kino in der Brotfabrik")
        link = rc.abs_url(_BONNER_KINEMATHEK_URL, href_m.group(1))

        description = ""
        if detail_fetcher:
            try:
                detail_html = detail_fetcher(link)
                description = _bonner_kinemathek_description(detail_html)
            except Exception as exc:
                common.log_source_error(
                    "Bonner Kinemathek", exc,
                    source_id=_BONNER_KINEMATHEK_SOURCE_ID,
                )

        if not description:
            description = common.factual_event_description(
                title, date_value=start, time_text=time_m.group(1) if time_m else "",
                venue=venue, city="Bonn",
            )
        if tags:
            description = common.concise_description(f"{description} Format: {tags}.")
        event = common.make_event(
            title,
            start,
            start,
            venue,
            "Bonn",
            description,
            link,
            "Bonner Kinemathek",
            f"cinema-special kino film festival open air filmgespräch {tags}",
            0.95,
            source_id=_BONNER_KINEMATHEK_SOURCE_ID,
        )
        if event:
            events.append(event)
    return events


def _bonner_kinemathek_description(html: str) -> str:
    notes = re.search(
        r'class="[^"]*em-event-notes[^"]*"[^>]*>(.*?)(?=<div[^>]*class="[^"]*(?:ticketurl|em-event-booking|em-event-location)|</section>|</main>|$)',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(rc.clean(notes.group(1) if notes else ""))


def _is_special_format(*values: str) -> bool:
    return bool(_SPECIAL_FORMAT_PATTERN.search(" ".join(value or "" for value in values)))


def _events_from_campus_kino(html: str) -> list:
    # The first group is the current semester; the later section is an archive.
    current_program = re.split(
        r'<div class="mod_article block" id="kino-semesterprogramm">',
        html or "",
        maxsplit=1,
        flags=re.I,
    )[0]
    events = []
    for body in re.findall(
        r'<div class="film-showcase"[^>]*>(.*?)(?=<div class="film-showcase"|<div class="mod_article block" id="kino-semesterprogramm"|$)',
        current_program,
        re.S | re.I,
    ):
        title_m = re.search(r'<h1 class="headline-h3">(.*?)</h1>', body, re.S | re.I)
        date_m = re.search(r'class="film-info-text datum">(.*?)</span>', body, re.S | re.I)
        time_m = re.search(r'class="film-info-text uhrzeit">(.*?)</span>', body, re.S | re.I)
        venue_m = re.search(r'class="film-info-text raum">(.*?)</span>', body, re.S | re.I)
        if not (title_m and date_m):
            continue
        title = rc.clean(title_m.group(1))
        date_text = rc.clean(date_m.group(1))
        time_text = rc.clean(time_m.group(1) if time_m else "")
        start = rc.with_time(common.parse_date(date_text), time_text)
        venue = rc.clean(venue_m.group(1) if venue_m else "Universität Bonn")
        paragraphs = [rc.clean(part) for part in re.findall(r'<p(?:\s[^>]*)?>(.*?)</p>', body, re.S | re.I)]
        description = " ".join(part for part in paragraphs if part)
        restriction = "Das Campus-Kino richtet sich an Studierende und Angehörige der Universität."
        description = common.concise_description(f"{restriction} {description}")
        event = common.make_event(
            title,
            start,
            start,
            venue,
            "Bonn",
            description,
            _CAMPUS_KINO_URL,
            "Campus-Kino Bonn",
            "cinema-special campus kino film hochschule studierende sonderprogramm",
            0.93,
            source_id=_CAMPUS_KINO_SOURCE_ID,
        )
        if event:
            events.append(event)
    return events


def _events_from_stummfilmtage(html: str) -> list:
    calendar_m = re.search(r'id="spielplan-calendar"(.*?)(?=</section>)', html or "", re.S | re.I)
    if not calendar_m:
        return []
    calendar = calendar_m.group(1)
    month_year_m = re.search(
        r'Programm\s*(?:<br\s*/?>|\s)+\s*([A-Za-zäöüÄÖÜ]+)\s+(20\d{2})',
        calendar,
        re.S | re.I,
    )
    if not month_year_m:
        month_year_m = re.search(r'([A-Za-zäöüÄÖÜ]+)\s+(20\d{2})', calendar, re.I)
    if not month_year_m:
        return []
    month = common.MONTH_DE.get(month_year_m.group(1).casefold())
    if not month:
        return []
    year = int(month_year_m.group(2))
    tab_days = {
        tab: int(day)
        for tab, day in re.findall(
            r'<a[^>]*data-w-tab="(Tab\s+\d+)"[^>]*>.*?<div class="headline-date">\s*(\d{1,2})\s*</div>',
            calendar,
            re.S | re.I,
        )
    }

    events = []
    panes = re.findall(
        r'<div data-w-tab="(Tab\s+\d+)" class="[^"]*w-tab-pane[^"]*">(.*?)(?=<div data-w-tab="Tab\s+\d+" class="[^"]*w-tab-pane|</div>\s*</div>\s*</div>\s*</section>|$)',
        calendar,
        re.S | re.I,
    )
    for tab, pane in panes:
        day = tab_days.get(tab)
        if not day:
            continue
        try:
            date_value = datetime(year, month, day)
        except ValueError:
            continue
        for body in re.findall(
            r'<div[^>]*role="listitem"[^>]*class="[^"]*collection-item-10[^"]*"[^>]*>(.*?)(?=<div[^>]*role="listitem"[^>]*class="[^"]*collection-item-10|$)',
            pane,
            re.S | re.I,
        ):
            href_m = re.search(r'<a[^>]*href="([^"]+)"', body, re.I)
            time_m = re.search(r'class="cms-datum">\s*(\d{1,2}:\d{2})\s*</div>', body, re.I)
            title_m = re.search(r'class="cms-headline">(.*?)</h3>', body, re.S | re.I)
            if not (href_m and title_m):
                continue
            title = rc.clean(title_m.group(1))
            if re.search(r"\bkeine\s+filmvorführung", title, re.I):
                continue
            time_text = time_m.group(1) if time_m else ""
            start = rc.with_time(date_value, time_text)
            metadata = [rc.clean(value) for value in re.findall(r'class="cms-text(?:\s+[^"]*)?">(.*?)</div>', body, re.S | re.I)]
            metadata = [value for value in metadata if value]
            description = " · ".join(metadata)
            is_side_program = title.casefold().startswith("rahmenprogramm")
            context = (
                "Rahmenprogramm der Internationalen Stummfilmtage. "
                if is_side_program else
                "Open-Air-Stummfilmfestival mit Livemusik. "
            )
            description = common.concise_description(" ".join(filter(None, [
                description,
                f"{context}Der Eintritt ist frei; Spenden werden erbeten.",
            ])))
            venue = (
                "Internationale Stummfilmtage – Rahmenprogramm"
                if is_side_program
                else "Arkadenhof Universität Bonn"
            )
            event = common.make_event(
                title,
                start,
                start,
                venue,
                "Bonn",
                description,
                rc.abs_url(_STUMMFILMTAGE_URL, href_m.group(1)),
                "Internationale Stummfilmtage",
                "cinema-special stummfilm festival open air kino livemusik",
                0.98,
                source_id=_STUMMFILMTAGE_SOURCE_ID,
            )
            if event:
                events.append(event)
    return events


def _events_from_kinopolis_listing(html: str, detail_fetcher=None) -> list:
    links = []
    for href in re.findall(r'href="([^"]*/bn/events/detail/[^"]+)"', html or "", re.I):
        link = rc.abs_url(_KINOPOLIS_URL, href)
        if link not in links:
            links.append(link)
    events = []
    for link in links:
        if not detail_fetcher:
            continue
        try:
            events.extend(_events_from_kinopolis_detail(detail_fetcher(link), link))
        except Exception as exc:
            common.log_source_error(
                "KINOPOLIS Bonn", exc, source_id=_KINOPOLIS_SOURCE_ID,
            )
    return rc.dedupe(events)


def _events_from_kinopolis_detail(html: str, detail_url: str) -> list:
    title_m = re.search(r'<h1 class="hl--1">(.*?)</h1>', html or "", re.S | re.I)
    if not title_m:
        return []
    title = rc.clean(title_m.group(1))
    description_m = re.search(
        r'<div class="container series_text">.*?'
        r'<h1 class="hl--1">.*?</h1>(.*?)'
        r'(?=<div class="slider[^>]*prog-nav|<div class="slider-6-program|'
        r'<div class="container">\s*<h1 class="hl--1">Weitere Events|$)',
        html,
        re.S | re.I,
    )
    description = rc.clean(description_m.group(1) if description_m else "")

    performance_dates = {}
    nav_pattern = re.compile(
        r'data-performance-ids=\[([^\]]+)\].*?<div class="prog-nav__day">\s*[^<]*?(\d{1,2})\.(\d{1,2})\.',
        re.S | re.I,
    )
    for ids_text, day, month in nav_pattern.findall(html):
        date_value = rc.date_for_window(int(day), int(month))
        for performance_id in re.findall(r"[A-Za-z0-9]+", ids_text):
            performance_dates[performance_id] = date_value

    events = []
    blocks = re.findall(
        r'<div class="prog2__cont[^"]*" data-performance-id="([^"]+)">(.*?)(?=<div class="prog2__cont|<p[^>]*>\s*Legende|<div class="prog2__caption|$)',
        html,
        re.S | re.I,
    )
    for performance_id, body in blocks:
        label_m = re.search(
            r'<div class="event-icon[^"]*prog__label-event[^"]*">(.*?)</div>',
            body,
            re.S | re.I,
        )
        time_m = re.search(r'class="prog2__time[^"]*"[^>]*>\s*(\d{1,2}:\d{2})\s*</a>', body, re.S | re.I)
        if not (label_m and time_m):
            continue
        special_label = rc.clean(label_m.group(1))
        if not special_label:
            continue
        start = rc.with_time(performance_dates.get(performance_id), time_m.group(1))
        hall_m = re.search(r'class="prog2__hall-num[^"]*"[^>]*>\s*<div>(.*?)</div>', body, re.S | re.I)
        price_m = re.search(r'class="buy__btn-in">\s*(.*?)\s*<br', body, re.S | re.I)
        price_text = rc.clean(price_m.group(1) if price_m else "")
        details = [f"Sonderformat: {special_label}.", description]
        if price_text:
            details.append(f"Tickets {price_text}.")
        venue = "KINOPOLIS Bonn-Bad Godesberg"
        hall = rc.clean(hall_m.group(1) if hall_m else "")
        if hall:
            venue = f"{venue}, {hall}"
        event = common.make_event(
            title,
            start,
            start,
            venue,
            "Bonn",
            common.concise_description(" ".join(value for value in details if value)),
            detail_url,
            "KINOPOLIS Bonn",
            f"cinema-special kino film event preview live {special_label}",
            0.94,
            source_id=_KINOPOLIS_SOURCE_ID,
        )
        if event:
            events.append(event)
    return events


def _fetch_filmhaus_events() -> list:
    berlin = ZoneInfo("Europe/Berlin")
    window_start = common.TODAY.replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=berlin,
    ).astimezone(timezone.utc)
    window_end = common.END_DATE.replace(
        hour=23, minute=59, second=59, microsecond=999999, tzinfo=berlin,
    ).astimezone(timezone.utc)
    query = urllib.parse.urlencode({
        "_sort": "date:ASC",
        "date_gte": window_start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "date_lte": window_end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "_limit": 100,
    })
    url = f"{_FILMHAUS_API}?{query}"
    try:
        raw = common.fetch_url(
            url,
            timeout=20,
            accept="application/json,*/*;q=0.8",
            sec_fetch_mode="cors",
            sec_fetch_dest="empty",
            expected_content_types=("application/json",),
        )
        return _events_from_filmhaus_json(raw)
    except Exception as exc:
        common.log_source_error(
            "Filmhaus Köln", exc, source_id=_FILMHAUS_SOURCE_ID,
        )
        return []


def _events_from_filmhaus_json(raw: str) -> list:
    try:
        records = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(records, list):
        return []
    events = []
    for record in records:
        if not isinstance(record, dict):
            continue
        tags = [
            rc.clean(tag.get("title", ""))
            for tag in (record.get("tags") or [])
            if isinstance(tag, dict)
        ]
        special_tags = [tag for tag in tags if _FILMHAUS_SPECIAL_TAG_PATTERN.search(tag)]
        if not special_tags:
            continue
        title = rc.clean(record.get("title", ""))
        start = _filmhaus_datetime(record.get("date", ""))
        end = _filmhaus_datetime(record.get("end_date", "")) or start
        description = rc.clean(record.get("description", ""))
        formats = ", ".join(special_tags)
        description = common.concise_description(f"Sonderformat: {formats}. {description}")
        venue = "Filmhaus Köln"
        if any(re.search(r"\bopen air\b", tag, re.I) for tag in special_tags):
            venue = "Filmhaus Köln – Open-Air-Kino"
        slug = urllib.parse.quote(str(record.get("slug") or "").strip())
        link = f"https://www.filmhaus-koeln.de/event/{slug}" if slug else "https://www.filmhaus-koeln.de/kino"
        event = common.make_event(
            title,
            start,
            end,
            venue,
            "Köln",
            description,
            link,
            "Filmhaus Köln",
            f"cinema-special kino film sonderveranstaltung {formats}",
            0.95,
            source_id=_FILMHAUS_SOURCE_ID,
        )
        if event:
            events.append(event)
    return rc.dedupe(events)


def _filmhaus_datetime(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(ZoneInfo("Europe/Berlin")).replace(tzinfo=None)


def _events_from_kurzfilmwanderung(html: str) -> list:
    heading_m = re.search(
        r'<h1[^>]*>(.*?KURZFILMWANDERUNG\s+BONN\s+20\d{2}.*?)</h1>',
        html or "",
        re.S | re.I,
    )
    if not heading_m:
        return []
    heading = rc.clean(heading_m.group(1))
    title_m = re.search(r'(KURZFILMWANDERUNG\s+BONN\s+20\d{2})', heading, re.I)
    date_m = re.search(r'\d{1,2}\.\s*[A-Za-zäöüÄÖÜ]+\s+20\d{2}', heading)
    time_m = re.search(r'(?:ab\s*)?(\d{1,2})(?::(\d{2}))?\s*Uhr', heading, re.I)
    if not (title_m and date_m):
        return []
    start = common.parse_date(date_m.group(0))
    if start and time_m:
        start = start.replace(hour=int(time_m.group(1)), minute=int(time_m.group(2) or 0))
    venue_m = re.search(r'Uhr\s+(Bonn\s+[^|]+)$', heading, re.I)
    venue = rc.clean(venue_m.group(1) if venue_m else "Bonn")
    intro_m = re.search(
        r'<p[^>]*>\s*<strong>Was ist die Kurzfilmwanderung Bonn\?</strong>(.*?)</p>',
        html,
        re.S | re.I,
    )
    description = rc.clean(intro_m.group(1) if intro_m else "")
    event = common.make_event(
        rc.clean(title_m.group(1)).title(),
        start,
        start,
        venue,
        "Bonn",
        description,
        _KURZFILMWANDERUNG_URL,
        "Kurzfilmwanderung Bonn",
        "cinema-special kurzfilm festival stadtspaziergang open air kino filmgespräch",
        0.96,
        source_id=_KURZFILMWANDERUNG_SOURCE_ID,
    )
    return [event] if event else []


def _fetch_kulturbad_cinema() -> list:
    try:
        events = common.fetch_ical(
            _KULTURBAD_ICAL,
            "Rüngsdorfer Kulturbad",
            "Bonn",
            "cinema-special kino film festival open air",
            0.95,
            source_id=_KULTURBAD_SOURCE_ID,
            event_filter=_kulturbad_is_cinema,
        )
    except Exception as exc:
        common.log_source_error(
            "Rüngsdorfer Kulturbad", exc, source_id=_KULTURBAD_SOURCE_ID,
        )
        return []
    return events


def _kulturbad_is_cinema(props: dict, _start, _end) -> bool:
    return bool(_KULTURBAD_CINEMA_PATTERN.search(" ".join([
        props.get("SUMMARY", ""),
        props.get("DESCRIPTION", ""),
        props.get("CATEGORIES", ""),
    ])))
