"""Organizer-authored martial-arts tournaments published through Kihapp."""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime

from .. import common
from . import regional_common as rc


URL = "https://www.kihapp.com/tournaments?country=Germany"
SOURCE = "Kihapp – Veranstalterdaten"
SOURCE_ID = "kihapp"
_MAX_PAGES = 20
_XHR_HEADERS = {"X-Requested-With": "XMLHttpRequest"}
_XHR_ACCEPT = "text/javascript, application/javascript, */*; q=0.01"
_REVIEWED_VENUE_OVERRIDES = {
    # Updated GSBA invitation, verified 2026-08-11:
    # https://www.gsbaworld.org/_files/ugd/6e62eb_43249bc0d95c4e90a5d8cb61bcbf2bc0.pdf
    "https://www.kihapp.com/tournaments/23960-6th-gsba-world-championships": (
        "Sportpark Nord, Kölnstraße 250, 53117 Bonn",
        "Kölnstraße 250, 53117 Bonn",
    ),
}
_REVIEWED_OCCURRENCES = (
    {
        "title": "6th GSBA World Championships",
        "date_text": "Aug 11 to 16, 2026",
        "start": datetime(2026, 8, 11),
        "end": datetime(2026, 8, 16),
        "venue": "Sportpark Nord Bonn",
        "link": "https://www.kihapp.com/tournaments/23960-6th-gsba-world-championships",
    },
)


def _decoded_listing_html(payload: str) -> str:
    """Turn Kihapp's escaped Rails XHR response back into parseable HTML."""
    decoded = html_lib.unescape(payload or "")
    return (
        decoded.replace(r"\n", "\n")
        .replace(r"\'", "'")
        .replace(r'\"', '"')
        .replace(r"<\/", "</")
        .replace(r"\/", "/")
    )


def _month_day(value: str, year: str) -> datetime | None:
    for pattern in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(f"{value} {year}", pattern)
        except ValueError:
            continue
    return None


def _date_range(text: str) -> tuple[datetime | None, datetime | None]:
    cleaned = rc.clean(text)
    explicit = re.search(
        r"\b([A-Za-z]+\s+\d{1,2}),\s*(20\d{2})\s*(?:to|[-–—])\s*"
        r"([A-Za-z]+\s+\d{1,2}),\s*(20\d{2})\b",
        cleaned,
        re.I,
    )
    if explicit:
        start = _month_day(explicit.group(1), explicit.group(2))
        end = _month_day(explicit.group(3), explicit.group(4))
        return (start, end) if start and end and end >= start else (None, None)
    match = re.search(
        r"\b([A-Za-z]+)\s+(\d{1,2})"
        r"(?:\s*(?:to|[-–—])\s*(?:([A-Za-z]+)\s+)?(\d{1,2}))?"
        r",\s*(20\d{2})\b",
        cleaned,
        re.I,
    )
    if not match:
        return None, None
    start_month, start_day, end_month, end_day, year = match.groups()
    start = _month_day(f"{start_month} {start_day}", year)
    if not start:
        return None, None
    if not end_day:
        return start, start
    end = _month_day(f"{end_month or start_month} {end_day}", year)
    if not end:
        return None, None
    if end < start:
        try:
            start = start.replace(year=start.year - 1)
        except ValueError:
            return None, None
    return start, end


def _listing_candidates(payload: str) -> list[dict]:
    html = _decoded_listing_html(payload)
    candidates = []
    for row in re.findall(r"<tr\b[^>]*data-upcoming[^>]*>.*?</tr>", html, re.S | re.I):
        link_match = re.search(
            r"<a\b[^>]*href=[\"'](/tournaments/\d+[^\"'?#]*)[\"'][^>]*>(.*?)</a>",
            row,
            re.S | re.I,
        )
        date_match = re.search(r"<span\b[^>]*class=[\"'][^\"']*\bdates\b[^\"']*[\"'][^>]*>(.*?)</span>", row, re.S | re.I)
        location_match = re.search(
            r"<div\b[^>]*class=[\"'][^\"']*\blocation\b[^\"']*[\"'][^>]*>(.*?)</div>",
            row,
            re.S | re.I,
        )
        if not (link_match and date_match and location_match):
            continue
        start, end = _date_range(date_match.group(1))
        title = rc.clean(link_match.group(2))
        if not (title and start):
            continue
        candidates.append({
            "title": title,
            "start": start,
            "end": end or start,
            "date_text": rc.clean(date_match.group(1)),
            "venue": re.sub(r",\s*Germany\s*$", "", rc.clean(location_match.group(1)), flags=re.I),
            "link": rc.abs_url(URL, link_match.group(1)),
        })
    return candidates


def _next_page(payload: str) -> int | None:
    html = _decoded_listing_html(payload)
    match = re.search(
        r"<a\b[^>]*\brel=[\"']next[\"'][^>]*\bhref=[\"'][^\"']*[?&]page=(\d+)[^\"']*[\"']",
        html,
        re.I,
    )
    return int(match.group(1)) if match else None


def _valid_non_upcoming_page(payload: str) -> bool:
    html = _decoded_listing_html(payload)
    rows = re.findall(r"<tr\b[^>]*>.*?</tr>", html, re.S | re.I)
    tournament_rows = [row for row in rows if re.search(r"/tournaments/\d+", row, re.I)]
    return bool(tournament_rows) and all(
        not re.search(r"<tr\b[^>]*\bdata-upcoming\b", row, re.I)
        for row in tournament_rows
    )


def _detail_venue(html: str, fallback: str) -> str:
    match = re.search(
        r"<p\b[^>]*class=[\"'][^\"']*\blocation\b[^\"']*[\"'][^>]*>(.*?)</p>",
        html or "",
        re.S | re.I,
    )
    return rc.clean(match.group(1)) if match else fallback


def _detail_dates(html: str, fallback: str) -> str:
    match = re.search(
        r"<span\b[^>]*class=[\"'][^\"']*\bdates\b[^\"']*[\"'][^>]*>(.*?)</span>",
        html or "",
        re.S | re.I,
    )
    return rc.clean(match.group(1)) if match else fallback


def _detail_coords(html: str) -> tuple[float, float] | None:
    match = re.search(
        r"<div\b(?=[^>]*\bclass=[\"'][^\"']*\bmap-container\b[^\"']*[\"'])"
        r"(?=[^>]*\bdata-latitude=[\"']([^\"']+)[\"'])"
        r"(?=[^>]*\bdata-longitude=[\"']([^\"']+)[\"'])[^>]*>",
        html or "",
        re.S | re.I,
    )
    if not match:
        return None
    try:
        return float(match.group(1)), float(match.group(2))
    except (TypeError, ValueError):
        return None


def _meta_description(html: str) -> str:
    for tag in re.findall(r"<meta\b[^>]*>", html or "", re.I | re.S):
        name = (rc.html_attribute(tag, "name") or rc.html_attribute(tag, "property")).casefold()
        if name in {"description", "og:description"}:
            return rc.clean(rc.html_attribute(tag, "content"))
    return ""


def _description(html: str, candidate: dict, start: datetime, end: datetime, venue: str, city: str) -> str:
    description = _meta_description(html)
    date_text = _detail_dates(html, candidate["date_text"])
    if date_text:
        description = re.sub(rf"^{re.escape(date_text)}[.]?\s*", "", description, count=1, flags=re.I)
    description = re.sub(r"\s*Powered by Kihapp[.]?\s*$", "", description, flags=re.I).strip()
    if description:
        return common.concise_description(description)
    return common.factual_event_description(
        candidate["title"], date_value=start, venue=venue, city=city,
        calendar_name="Kihapp-Turnierkalender",
    )


def _event_from_detail(candidate: dict, html: str):
    reviewed_venue = _REVIEWED_VENUE_OVERRIDES.get(candidate["link"])
    venue = reviewed_venue[0] if reviewed_venue else _detail_venue(html, candidate["venue"])
    date_text = _detail_dates(html, candidate["date_text"])
    start, end = _date_range(date_text)
    start = start or candidate["start"]
    end = end or candidate["end"] or start
    coords = _detail_coords(html)
    city = common.guess_city_from_text(venue)
    if not city or not common.event_in_window_and_radius(start, end, city, coords):
        return None
    venue = re.sub(rf"(?:,|\s)+{re.escape(city)}\s*$", "", venue, flags=re.I).strip()
    description = _description(html, candidate, start, end, venue, city)
    event = common.make_event(
        candidate["title"], start, end, venue, city, description,
        candidate["link"], SOURCE,
        "kampfsport martial arts turnier sport stockkampf kickboxen", 0.97,
        coords=coords, all_day=True, source_id=SOURCE_ID,
        default_category_key="sports", category_locked=True,
        source_role="primary", link_kind="detail",
    )
    if event and reviewed_venue:
        event["venue_address"] = reviewed_venue[1]
    return event


def _listing_url(page: int) -> str:
    return URL if page == 1 else f"{URL}&page={page}"


def _fetch_candidate(candidate: dict, detail_fetcher):
    try:
        detail = detail_fetcher(candidate["link"], timeout=20)
        return _event_from_detail(candidate, detail)
    except Exception as exc:
        common.log_source_error(f"{SOURCE} detail", exc, source_id=SOURCE_ID)
        return _event_from_detail(candidate, "")


def fetch(*, listing_fetcher=None, detail_fetcher=None) -> list:
    listing_fetcher = listing_fetcher or common.fetch_url
    detail_fetcher = detail_fetcher or (
        lambda url, **kwargs: common.fetch_detail_url(
            url, cache_namespace="kihapp-tournament-detail", **kwargs,
        )
    )
    events = []
    page = 1
    seen_links = set()
    while page <= _MAX_PAGES:
        endpoint = _listing_url(page)
        kwargs: dict[str, object] = {"timeout": 25}
        if page > 1:
            kwargs.update({"headers": _XHR_HEADERS, "accept": _XHR_ACCEPT, "sec_fetch_mode": "cors"})
        try:
            payload = listing_fetcher(endpoint, **kwargs)
            candidates = _listing_candidates(payload)
            parser_empty = not candidates and not _valid_non_upcoming_page(payload)
            common._record_endpoint(
                endpoint, parser_type="kihapp-listing",
                candidate_count=len(candidates), parsed_event_count=len(candidates),
                parser_empty=parser_empty,
            )
            if not candidates:
                if parser_empty:
                    common.log_source_error(
                        SOURCE, rc.ParserEmptyError("parser returned no tournament records"),
                        source_id=SOURCE_ID,
                    )
                break
            for candidate in candidates:
                if candidate["link"] in seen_links:
                    continue
                seen_links.add(candidate["link"])
                if not common.window_contains(candidate["start"], candidate["end"]):
                    continue
                event = _fetch_candidate(candidate, detail_fetcher)
                if event:
                    events.append(event)
            next_page = _next_page(payload)
            if not next_page or next_page <= page:
                break
            if any(candidate["start"] > common.END_DATE for candidate in candidates):
                break
            page = next_page
        except Exception as exc:
            common.log_source_error(SOURCE, exc, source_id=SOURCE_ID)
            break
    emitted_links = {event.get("link") for event in events}
    for candidate in _REVIEWED_OCCURRENCES:
        if candidate["link"] in emitted_links:
            continue
        if not common.window_contains(candidate["start"], candidate["end"]):
            continue
        event = _fetch_candidate(candidate, detail_fetcher)
        if event:
            events.append(event)
    return rc.dedupe_occurrences(events)
