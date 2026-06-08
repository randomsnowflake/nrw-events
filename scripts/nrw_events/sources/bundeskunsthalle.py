"""
Bundeskunsthalle — current exhibitions (Bonn Museum Mile).

Reads:  bundeskunsthalle.de/en/exhibitions
Yields: current/upcoming exhibitions, scraped live from the page's heading pairs:
        a title heading followed by a date-range heading, e.g.
        "Peter Hujar Eyes Open in the Dark" / "27 February to 23 August 2026".

The page has no JSON-LD, so this parses the rendered headings. Exhibition titles
and dates are discovered live — nothing is hardcoded.
"""

import re
from datetime import datetime
from html import unescape

from .. import common

_URL = "https://www.bundeskunsthalle.de/en/exhibitions"

# "27 February to 23 August 2026", "11 October 2026 to 2 May 2027",
# "1 May to 1 November 2026", "until 23 August 2026"
_RANGE_RE = re.compile(
    r"^(?:(?P<sd>\d{1,2})\s+(?P<sm>[A-Za-z]+)(?:\s+(?P<sy>20\d{2}))?\s+to\s+)?"
    r"(?:until\s+)?(?P<ed>\d{1,2})\s+(?P<em>[A-Za-z]+)\s+(?P<ey>20\d{2})$",
    re.I,
)


def _headings(html: str) -> list:
    out = []
    for m in re.finditer(r"<h[1-4][^>]*>(.*?)</h[1-4]>", html, re.S):
        t = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1)))).strip()
        if t:
            out.append(t)
    return out


def _parse_range(text: str):
    m = _RANGE_RE.match(text.strip())
    if not m:
        return None, None
    em = common.MONTH_EN.get(m.group("em").lower())
    if not em:
        return None, None
    ey = int(m.group("ey"))
    try:
        end = datetime(ey, em, int(m.group("ed")))
    except ValueError:
        return None, None
    if m.group("sd") and m.group("sm"):
        sm = common.MONTH_EN.get(m.group("sm").lower())
        if not sm:
            return None, end
        sy = int(m.group("sy") or ey)
        try:
            return datetime(sy, sm, int(m.group("sd"))), end
        except ValueError:
            return None, end
    return None, end  # "until <date>" → open start


def _tidy_title(t: str) -> str:
    # Headings concatenate title + subtitle spans with no space ("HujarEyes").
    return re.sub(r"([a-zäöüß])([A-ZÄÖÜ])", r"\1 \2", t).strip()


def fetch() -> list:
    source = "Bundeskunsthalle"
    try:
        headings = _headings(common.fetch_url(_URL, timeout=25))
    except Exception as e:
        common.log_source_error(source, e)
        return []

    events = []
    for i in range(1, len(headings)):
        start_dt, end_dt = _parse_range(headings[i])
        if not end_dt:
            continue
        title = _tidy_title(headings[i - 1])
        if len(title) < 3 or _RANGE_RE.match(title):
            continue
        # Treat as an exhibition spanning [start, end]; make_event keeps it if the
        # span overlaps the window. Open-start exhibitions use TODAY as start.
        ev = common.make_event(
            title, start_dt or common.TODAY, end_dt,
            "Bundeskunsthalle", "Bonn",
            "Museum Mile, Helmut-Kohl-Allee 4", _URL,
            source, "exhibition museum art", 1.0,
        )
        if ev:
            events.append(ev)
    return events
