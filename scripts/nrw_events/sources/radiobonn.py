"""
Radio Bonn/Rhein-Sieg — editorial weekly event tips.

The page is not a structured calendar, but it reliably publishes hand-picked
local Bonn/Rhein-Sieg events in simple paragraphs where the title/date is marked
as ``<strong><u>… - DD.MM.YYYY</u></strong>`` followed by one description
paragraph. This catches small local events that municipal feeds often miss.
"""

import re
from html import unescape
from urllib.parse import urljoin, urlsplit

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

_ANCHOR_LINK_RE = re.compile(r'<a\b[^>]*\bhref=["\']([^"\']+)["\']', re.I)
_BARE_DOMAIN_RE = re.compile(
    r"(?<![@\w])((?:https?://|www\.)?(?:[a-z0-9-]+\.)+(?:de|com|org)"
    r"(?:/[^\s<]*)?)",
    re.I,
)

_FULL_RANGE_SUFFIX_RE = re.compile(
    r"\s+-\s+(?P<start_day>\d{1,2})\.(?P<start_month>\d{1,2})\."
    r"(?P<start_year>20\d{2})?\s*(?:-|–|—|&|und|bis|/)\s*"
    r"(?P<end_day>\d{1,2})\.(?P<end_month>\d{1,2})\.(?P<end_year>20\d{2})\s*$",
    re.I,
)
_SAME_MONTH_RANGE_SUFFIX_RE = re.compile(
    r"\s+-\s+(?P<start_day>\d{1,2})\.\s*(?:-|–|—|&|und|bis|/)\s*"
    r"(?P<end_day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>20\d{2})\s*$",
    re.I,
)
_SINGLE_DATE_SUFFIX_RE = re.compile(
    r"\s+-\s+(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>20\d{2})\s*$"
)


def _hinted_city(text: str) -> str | None:
    lower = (text or "").lower()
    matches = [(needle, city) for needle, city in _CITY_HINTS.items() if needle in lower]
    if matches:
        # The station name and descriptions mention Bonn frequently. Prefer a
        # more specific municipality when both are present, then the longest
        # matching place name (e.g. Bad Honnef over Honnef-like fragments).
        matches.sort(key=lambda item: (item[1] == "Bonn", -len(item[0])))
        return matches[0][1]
    return None


def _city_for(text: str) -> str:
    lower = (text or "").lower()
    meeting_point = re.search(r"[^.!?]*\btreffpunkt\b[^.!?]*", lower)
    if meeting_point:
        meeting_point_text = meeting_point.group(0)
        if city := _hinted_city(meeting_point_text):
            return city
        if city := common.guess_city_from_text(meeting_point_text):
            return city
    if city := _hinted_city(lower):
        return city
    return common.guess_city_from_text(text) or "Bonn"


def _venue_for(text: str, city: str) -> str:
    for venue in _VENUE_HINTS:
        if venue.lower() in (text or "").lower():
            if venue == "Marktplatz" and city == "Bonn":
                return "Bonner Marktplatz"
            return venue
    return city


def _split_title_dates(raw_title: str):
    """Return a clean title plus inclusive start/end dates from its suffix."""
    raw_title = common.clean_html(raw_title)
    if match := _FULL_RANGE_SUFFIX_RE.search(raw_title):
        end_year = int(match.group("end_year"))
        start_month = int(match.group("start_month"))
        end_month = int(match.group("end_month"))
        start_year = int(match.group("start_year") or (
            end_year - 1 if start_month > end_month else end_year
        ))
        start = common.parse_date(
            f'{match.group("start_day")}.{start_month}.{start_year}'
        )
        end = common.parse_date(
            f'{match.group("end_day")}.{end_month}.{end_year}'
        )
        return raw_title[:match.start()].strip() or raw_title, start, end

    if match := _SAME_MONTH_RANGE_SUFFIX_RE.search(raw_title):
        start = common.parse_date(
            f'{match.group("start_day")}.{match.group("month")}.{match.group("year")}'
        )
        end = common.parse_date(
            f'{match.group("end_day")}.{match.group("month")}.{match.group("year")}'
        )
        return raw_title[:match.start()].strip() or raw_title, start, end

    if match := _SINGLE_DATE_SUFFIX_RE.search(raw_title):
        start = common.parse_date(
            f'{match.group("day")}.{match.group("month")}.{match.group("year")}'
        )
        return raw_title[:match.start()].strip() or raw_title, start, None

    return raw_title, common.parse_date(raw_title), None


def _split_title_date(raw_title: str):
    """Backward-compatible single-date view used by older adapter callers."""
    title, start, _ = _split_title_dates(raw_title)
    return title, start


def _external_web_link(raw_link: str) -> str:
    link = common.normalize_url(urljoin(URL, unescape(raw_link or "").strip()))
    parsed = urlsplit(link)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    if parsed.scheme not in {"http", "https"} or not hostname:
        return ""
    if hostname == "radiobonn.de" or hostname.endswith(".radiobonn.de"):
        return ""
    return link


def _best_event_link(raw_description: str) -> str:
    """Prefer an embedded event/organizer destination over the Radio index."""
    for raw_link in _ANCHOR_LINK_RE.findall(raw_description or ""):
        if link := _external_web_link(raw_link):
            return link

    description = common.clean_html(raw_description)
    for match in _BARE_DOMAIN_RE.finditer(description):
        raw_link = match.group(1).rstrip(".,;:!?)]}\"")
        if not raw_link.startswith(("http://", "https://")):
            raw_link = "https://" + raw_link
        if link := _external_web_link(raw_link):
            return link
    return URL


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
        title, start_dt, end_dt = _split_title_dates(raw_title)
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
            end_dt=end_dt,
            venue=venue,
            city=city,
            description=desc,
            link=_best_event_link(raw_desc),
            source=source,
            category=category,
            trust=0.72,
            time_text=time_text,
        )
        if ev:
            events.append(ev)
    return events
