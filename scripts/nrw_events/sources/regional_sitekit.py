"""SiteKit teaser calendars for Brühl and Wesseling."""

import re

from .. import common
from . import regional_common as rc

_SOURCE = "SiteKit regional"
_CALENDARS = [
    ("Brühl", "sitekit-bruehl", "https://www.bruehl.de/tksf/veranstaltungskalender/veranstaltungskalender.php", 0.9),
    ("Wesseling", "sitekit-wesseling", "https://www.wesseling.de/kultur-sport/veranstaltungskalender.php", 0.86),
]


def fetch() -> list:
    events = []
    for city, source_id, url, trust in _CALENDARS:
        events.extend(rc.fetch_html_events(
            f"{_SOURCE} ({city})",
            url,
            lambda html, city=city, source_id=source_id, url=url, trust=trust: _events_from_teasers(
                html, url, city, trust, source_id
            ),
            source_id=source_id,
        ))
    return rc.dedupe(events)


def _events_from_teasers(html: str, base: str, city: str, trust: float,
                         source_id: str) -> list:
    events = []
    for block in re.findall(r'<article class="SP-Teaser.*?</article>', html, re.S | re.I):
        href = re.search(r'<a[^>]+class="SP-Teaser__inner"[^>]+href="([^"]+)"', block, re.S | re.I)
        date = re.search(r'<span class="SP-Scheduling__date">([^<]+)', block, re.S | re.I)
        title = re.search(r'<h4 class="SP-Teaser__headline">(.*?)</h4>', block, re.S | re.I)
        desc = re.search(r'<div class="SP-Teaser__abstract">(.*?)</div>', block, re.S | re.I)
        if not (date and title):
            continue
        text = rc.clean(block)
        start = rc.with_time(rc.parse_dt(date.group(1)), text)
        ev = common.make_event(
            rc.clean(title.group(1)),
            start,
            start,
            city,
            city,
            rc.clean(desc.group(1) if desc else ""),
            rc.abs_url(base, href.group(1) if href else ""),
            _SOURCE,
            "kommunal kultur markt ausstellung konzert führung",
            trust,
            rc.time_text(text),
            source_id=source_id,
        )
        if ev:
            events.append(ev)
    return events
