"""Official programme table for Theater im Ballsaal."""

import re
from urllib.parse import urlparse

from .. import common
from . import regional_common as rc


_SOURCE = "Theater im Ballsaal"
_CALENDAR = "https://theater-im-ballsaal.de/spielplan/"
_CATEGORY = "theater bühne performance tanz"
_TRUST = 1.0


def _meta_description(html: str) -> str:
    for pattern in (
        r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=(["\'])(.*?)\1',
        r'<meta[^>]+content=(["\'])(.*?)\1[^>]+(?:property|name)=["\'](?:og:description|description)["\']',
    ):
        match = re.search(pattern, html, re.I)
        if match:
            return common.concise_description(match.group(2))
    return ""


def _detail_description(url: str) -> str:
    if urlparse(url).hostname not in {"theater-im-ballsaal.de", "www.theater-im-ballsaal.de"}:
        return ""
    try:
        return _meta_description(common.fetch_detail_url(
            url, cache_namespace="theater-im-ballsaal", timeout=15
        ))
    except Exception as exc:
        common.log_source_error(f"{_SOURCE} detail", exc)
        return ""


def _category(genre: str) -> str:
    lowered = genre.casefold()
    if "konzert" in lowered and not any(word in lowered for word in ("theater", "tanz", "performance")):
        return "konzert musik"
    if "workshop" in lowered:
        return "workshop kreativ"
    return _CATEGORY


def events_from_html(html: str, detail_fetcher=None) -> list[dict]:
    detail_fetcher = detail_fetcher or _detail_description
    events = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.I | re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.I | re.S)
        if len(cells) < 5:
            continue
        date_match = re.search(r'(\d{1,2})\.(\d{1,2})\.', rc.clean(cells[1]))
        if not date_match:
            continue
        day, month = (int(part) for part in date_match.groups())
        date_value = rc.date_for_window(day, month)
        time_match = re.search(r'(\d{1,2})[.:](\d{2})', rc.clean(cells[2]))
        time_text = f"{int(time_match.group(1)):02d}:{time_match.group(2)}" if time_match else ""
        start_dt = rc.with_time(date_value, time_text)
        link_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', cells[3], re.I | re.S)
        if not link_match or not start_dt:
            continue
        href, title_html = link_match.groups()
        title = rc.clean(title_html)
        link = rc.abs_url(_CALENDAR, href)
        genre = rc.clean(cells[4])
        cell_text = rc.clean(cells[3])
        summary = cell_text[len(title):].strip(" |–-") if cell_text.startswith(title) else cell_text
        description = detail_fetcher(link) or summary
        if genre and genre.casefold() not in description.casefold():
            description = common.concise_description(f"{description} {genre}")
        if not description:
            description = common.factual_event_description(
                title, date_value=start_dt, time_text=time_text,
                venue="Theater im Ballsaal", city="Bonn",
            )
        event = common.make_event(
            title, start_dt, None, "Theater im Ballsaal", "Bonn", description,
            link, _SOURCE, _category(genre), _TRUST, time_text,
            source_id="theater-im-ballsaal",
        )
        if event:
            events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list[dict]:
    return rc.fetch_html_events(_SOURCE, _CALENDAR, events_from_html)
