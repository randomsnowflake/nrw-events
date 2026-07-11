"""
Radio Bonn/Rhein-Sieg — editorial weekly event tips.

The page is not a structured calendar, but it reliably publishes hand-picked
local Bonn/Rhein-Sieg events in simple paragraphs where the title/date is marked
as ``<strong><u>… - DD.MM.YYYY</u></strong>`` followed by one description
paragraph. This catches small local events that municipal feeds often miss.
"""

import re
from html import unescape

from .. import common

URL = "https://www.radiobonn.de/artikel/was-geht-unsere-veranstaltungstipps-2674962"


_CITY_HINTS = {
    "bonn": "Bonn",
    "beuel": "Bonn",
    "bad godesberg": "Bonn",
    "tannenbusch": "Bonn",
    "auerberg": "Bonn",
    "münsterplatz": "Bonn",
    "muensterplatz": "Bonn",
    "marktplatz in bonn": "Bonn",
    "siegburg": "Siegburg",
    "troisdorf": "Troisdorf",
    "sankt augustin": "Sankt Augustin",
    "hennef": "Hennef",
    "königswinter": "Königswinter",
    "koenigswinter": "Königswinter",
    "bad honnef": "Bad Honnef",
    "meckenheim": "Meckenheim",
    "wachtberg": "Wachtberg",
    "alfter": "Alfter",
    "eitorf": "Eitorf",
    "niederkassel": "Niederkassel",
}

_VENUE_HINTS = [
    "Münsterplatz", "Marktplatz", "Bonner Marktplatz", "Pantheon", "Telekom Dome",
    "Rheinaue", "Bundeskunsthalle", "Kunstmuseum", "Brotfabrik", "Harmonie",
    "GOP", "Katharinenhof", "Mühlenbachhalle", "Stadthalle", "Dorfplatz",
    "Sportpark Nord", "Siegburger Marktplatz", "Eitorfer Marktplatz",
]


def _city_for(text: str) -> str:
    lower = (text or "").lower()
    matches = [(needle, city) for needle, city in _CITY_HINTS.items() if needle in lower]
    if matches:
        # The station name and descriptions mention Bonn frequently. Prefer a
        # more specific municipality when both are present, then the longest
        # matching place name (e.g. Bad Honnef over Honnef-like fragments).
        matches.sort(key=lambda item: (item[1] == "Bonn", -len(item[0])))
        return matches[0][1]
    return common.guess_city_from_text(text) or "Bonn"


def _venue_for(text: str, city: str) -> str:
    for venue in _VENUE_HINTS:
        if venue.lower() in (text or "").lower():
            if venue == "Marktplatz" and city == "Bonn":
                return "Bonner Marktplatz"
            return venue
    return city


def _date_from_title(title: str):
    # Prefer a fully specified DD.MM.YYYY anywhere in the title.
    m = re.search(r"\b\d{1,2}\.\d{1,2}\.20\d{2}\b", title or "")
    if m:
        return common.parse_date(m.group(0))
    # Handle compact ranges like "04. & 05.07.2026" by reconstructing the first date.
    m = re.search(r"\b(\d{1,2})\.\s*(?:&|und|/)\s*\d{1,2}\.(\d{1,2})\.(20\d{2})", title or "")
    if m:
        day, month, year = m.groups()
        return common.parse_date(f"{day}.{month}.{year}")
    return common.parse_date(title or "")


def _split_title_date(raw_title: str):
    raw_title = common.clean_html(raw_title)
    title = re.sub(r"\s+-\s+(?:\d{1,2}\.\s*(?:&|und|/)\s*)?\d{1,2}\.\d{1,2}\.20\d{2}\s*$", "", raw_title).strip()
    title = re.sub(r"\s+-\s+\d{1,2}\.\d{1,2}\.20\d{2}\s*$", "", title).strip()
    return title or raw_title, _date_from_title(raw_title)


def fetch() -> list:
    source = "Radio Bonn/Rhein-Sieg"
    try:
        html = common.fetch_url(URL, timeout=20)
    except Exception as e:
        common.log_source_error(source, e)
        return []

    # Match title paragraphs and the immediately following description paragraph.
    blocks = re.findall(
        r"<p>\s*<strong>\s*<u>(.*?)</u>\s*</strong>\s*</p>\s*<p>(.*?)</p>",
        html,
        flags=re.I | re.S,
    )

    events = []
    for raw_title, raw_desc in blocks:
        raw_title = unescape(raw_title)
        title, start_dt = _split_title_date(raw_title)
        if not start_dt:
            continue
        desc = common.clean_html(raw_desc)
        if not desc or len(desc) < 30:
            continue
        text = f"{title} {desc}"
        city = _city_for(text)
        venue = _venue_for(text, city)
        category = "Sport" if re.search(r"\bSport|Sportverein|Bewegung|Fechten|Turnen|Segeln\b", text, re.I) else "Event"
        time_text = ""
        m = re.search(r"\b(?:um|ab)\s+(\d{1,2})\s*Uhr\b", desc, re.I)
        if m:
            time_text = f"{int(m.group(1)):02d}:00"
        ev = common.make_event(
            title=title,
            start_dt=start_dt,
            end_dt=None,
            venue=venue,
            city=city,
            description=desc,
            link=URL,
            source=source,
            category=category,
            trust=0.72,
            time_text=time_text,
        )
        if ev:
            events.append(ev)
    return events
