"""
Königswinter — official town calendar (GeoCMS).

Reads:  koenigswinter.de/de/veranstaltungen.html
Yields: Siebengebirgsmuseum dates, guided tours, markets, hikes, small culture.
        Good Siebengebirge / Drachenfels coverage near Bonn.
"""

import re
import urllib.parse

from .. import common


def fetch() -> list:
    source = "Königswinter"
    url = "https://www.koenigswinter.de/de/veranstaltungen.html"
    try:
        html = common.fetch_url(url, timeout=25)
        events = []
        card_re = re.compile(
            r'<span class="text-muted">\s*(?P<cat>.*?)\s*</span>\s*'
            r'<h4>\s*<a href="(?P<link>[^"]+)">(?P<title>.*?)</a>\s*</h4>'
            r'(?:\s*<h6[^>]*>(?P<subtitle>.*?)</h6>)?'
            r'.*?<div class="mb-2">.*?</i>\s*(?P<date>\d{2}\.\d{2}\.20\d{2})'
            r'(?:\s*-\s*(?P<end>\d{2}\.\d{2}\.20\d{2}))?(?:\s*von\s*(?P<time>.*?))?\s*</div>'
            r'.*?<span class="gcevent-list-location-span">\s*(?P<venue>.*?)\s*</span>',
            re.S | re.I,
        )
        for m in card_re.finditer(html):
            category = (common.clean_html(m.group("cat"))
                        + " königswinter siebengebirge outdoor wanderung markt führung")
            ev = common.make_event(
                common.clean_html(m.group("title")),
                common.parse_date(m.group("date")),
                common.parse_date(m.group("end") or m.group("date")),
                common.clean_html(m.group("venue")),
                "Königswinter",
                common.clean_html(m.group("subtitle") or ""),
                urllib.parse.urljoin("https://www.koenigswinter.de/", m.group("link")),
                source, category, 0.95, common.clean_html(m.group("time") or "")[:80],
            )
            if ev:
                events.append(ev)
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []
