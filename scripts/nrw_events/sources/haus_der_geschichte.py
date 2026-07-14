"""Haus der Geschichte Bonn — official TYPO3 event calendar.

The calendar exposes event facts in server-rendered panels. Some entries are
organized by the museum but take place at external historical venues; preserve
that explicit venue instead of assigning every entry to the museum.
"""

import re
from datetime import datetime

from .. import common

_URL = "https://www.hdg.de/haus-der-geschichte/veranstaltungen"
_SOURCE = "Haus der Geschichte"
_DEFAULT_VENUE = "Haus der Geschichte"


def _text(value: str) -> str:
    return common.clean_html(value or "")


def _panel_blocks(html: str) -> list[str]:
    starts = [match.start() for match in re.finditer(r'<div class="panel\s+bonn"', html or "", re.I)]
    return [html[start:end] for start, end in zip(starts, starts[1:] + [len(html)])]


def events_from_html(html: str) -> list:
    events = []
    for panel in _panel_blocks(html):
        date_match = re.search(r'data-date="(20\d{6})"', panel, re.I)
        title_match = re.search(r"<h4[^>]*>(.*?)</h4>", panel, re.S | re.I)
        if not (date_match and title_match):
            continue

        time_match = re.search(r'class="calendar-events-time"[^>]*>(.*?)</div>', panel, re.S | re.I)
        time_text = _text(time_match.group(1) if time_match else "")
        clock = re.search(r"\b(\d{1,2}):(\d{2})\b", time_text)
        start = datetime.strptime(date_match.group(1), "%Y%m%d")
        if clock:
            start = start.replace(hour=int(clock.group(1)), minute=int(clock.group(2)))

        heading_match = re.search(r"<h6[^>]*>(.*?)</h6>", panel, re.S | re.I)
        heading = heading_match.group(1) if heading_match else ""
        venue_match = re.search(r'<span[^>]*class="black"[^>]*>(.*?)</span>', heading, re.S | re.I)
        explicit_venue = _text(venue_match.group(1) if venue_match else "")
        venue = explicit_venue or _DEFAULT_VENUE
        # External venues include a postal address in the heading. Keep the
        # entity name stable for downstream venue mapping.
        venue = re.split(r",\s*(?:[A-ZÄÖÜ][^,]+\s+)?\d{1,5}\b", venue, maxsplit=1)[0].strip()
        category = _text(re.sub(r"<span\b.*?</span>", "", heading, flags=re.S | re.I)) or "Museum"

        description_match = re.search(
            r'class="[^"]*calendar-bodycopy[^"]*"[^>]*>(.*?)</div>', panel, re.S | re.I
        )
        description = _text(description_match.group(1) if description_match else "")
        # Admission text sits in the panel heading outside the title tags. The
        # prefix ends before the body copy and therefore avoids repeating the
        # long event description while retaining labels such as "Eintritt frei".
        if description_match:
            description = " ".join(filter(None, [description, _text(panel[:description_match.start()])]))

        link_match = re.search(r'<a[^>]*class="hidden"[^>]*href="([^"]+)"', panel, re.S | re.I)
        link = common.urllib.parse.urljoin(_URL, link_match.group(1)) if link_match else _URL
        event = common.make_event(
            _text(title_match.group(1)), start, None, venue, "Bonn", description,
            link, _SOURCE, category, 1.0, start.strftime("%H:%M") if clock else "",
            all_day=not bool(clock),
        )
        if event:
            events.append(event)
    return events


def fetch() -> list:
    try:
        return events_from_html(common.fetch_url(_URL, timeout=30))
    except Exception as exc:
        common.log_source_error(_SOURCE, exc)
        return []
