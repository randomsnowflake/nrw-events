"""
Königswinter — official town calendar (GeoCMS).

Reads:  koenigswinter.de/de/veranstaltungskalender.html
Yields: Siebengebirgsmuseum dates, guided tours, markets, hikes, small culture.
        Good Siebengebirge / Drachenfels coverage near Bonn.
"""

import re
import urllib.parse

from .. import common
from . import regional_common as rc


_BASE_URL = "https://www.koenigswinter.de/"
_CALENDAR_URL = f"{_BASE_URL}de/veranstaltungskalender.html"
_CARD_START_RE = re.compile(r'(?=<li[^>]*class="[^"]*\bmedia\b[^"]*"[^>]*>)', re.I)


def fetch() -> list:
    source = "Königswinter"
    try:
        html = common.fetch_url(_CALENDAR_URL, timeout=25)
        return _events_from_listing(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="koenigswinter", timeout=15),
        )
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _events_from_listing(html: str, detail_fetcher=None) -> list:
    events = []
    for block in _listing_blocks(html):
        title_link = re.search(
            r'<h4[^>]*>\s*<a[^>]+href="(?P<link>[^"]+)"[^>]*>(?P<title>.*?)</a>\s*</h4>',
            block,
            re.S | re.I,
        )
        schedule = re.search(
            r'<div[^>]*class="[^"]*\bmb-2\b[^"]*"[^>]*>.*?</i>(.*?)</div>',
            block,
            re.S | re.I,
        )
        schedule_text = common.clean_html(schedule.group(1) if schedule else "")
        dates = re.findall(r"\d{2}\.\d{2}\.20\d{2}", schedule_text)
        if not (title_link and dates):
            continue
        title = common.clean_html(title_link.group("title"))
        start_date = dates[0]
        end_date = dates[1] if len(dates) > 1 else start_date
        times = re.findall(r"\d{1,2}:\d{2}", schedule_text)
        time_text = f"{times[0]} bis {times[1]}" if len(times) > 1 else (times[0] if times else "")
        venue = _card_text(block, "gcevent-list-location-span")
        source_category = _card_text(block, "text-muted")
        link = urllib.parse.urljoin(_BASE_URL, title_link.group("link"))
        listing_copy = _card_element_text(block, "h6")
        detail_copy = _detail_copy(link, detail_fetcher)
        description = detail_copy or listing_copy or _fallback_description(
            title,
            start_date,
            end_date,
            time_text,
            venue,
        )
        category = f"{source_category} königswinter siebengebirge"
        event = common.make_event(
            title,
            common.parse_date(start_date),
            common.parse_date(end_date),
            venue,
            "Königswinter",
            description,
            link,
            "Königswinter",
            category,
            0.95,
            time_text,
        )
        if event:
            events.append(event)
    return events


def _listing_blocks(html: str) -> list[str]:
    blocks = [block for block in _CARD_START_RE.split(html) if "/veranstaltungskalender/event/" in block]
    return blocks or [html]


def _card_text(block: str, class_name: str) -> str:
    return rc.first_group_clean(
        rf'<[^>]+class="[^"]*\b{class_name}\b[^"]*"[^>]*>(.*?)</[^>]+>',
        block,
    )


def _card_element_text(block: str, tag: str) -> str:
    return rc.first_group_clean(rf"<{tag}[^>]*>(.*?)</{tag}>", block)


def _detail_copy(link: str, detail_fetcher) -> str:
    if not (link and detail_fetcher):
        return ""
    try:
        html = detail_fetcher(link)
    except Exception as exc:
        common.log_source_error("Königswinter detail", exc)
        return ""
    content = re.search(
        r'<div[^>]*class="[^"]*\bevent-content\b[^"]*"[^>]*>(.*?)</div>',
        html,
        re.S | re.I,
    )
    description = common.clean_html(content.group(1) if content else "")
    if description and not re.search(r"[.!?][\"'»)]*$", description):
        description += "."
    return description


def _fallback_description(
    title: str,
    start_date: str,
    end_date: str,
    time_text: str,
    venue: str,
) -> str:
    if end_date and end_date != start_date:
        schedule = f"für den Zeitraum vom {start_date} bis {end_date}"
    else:
        schedule = f"für den {start_date}"
    times = re.findall(r"\d{1,2}:\d{2}", time_text or "")
    if len(times) >= 2:
        schedule += f" von {times[0]} bis {times[1]} Uhr"
    elif times:
        schedule += f" um {times[0]} Uhr"
    if venue:
        schedule += f" am Veranstaltungsort „{venue}“"
    return f"„{title}“ ist im Königswinterer Veranstaltungskalender {schedule} angekündigt."
