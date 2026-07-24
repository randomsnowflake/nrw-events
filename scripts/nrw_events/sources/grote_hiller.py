"""Direct market dates from the Grote & Hiller organizer listings."""

import re

from .. import common
from . import regional_common as rc


_BASE_URL = "https://www.grote-hiller.de"
_URLS = (
    f"{_BASE_URL}/troedelmaerkte/",
    f"{_BASE_URL}/stadtflohmaerkte/",
    f"{_BASE_URL}/maedelsflohmaerkte/",
    f"{_BASE_URL}/fashion-family/",
)
_CITY_ALIASES = {
    "denklingen": "Reichshof",
}


def _listing_city(title: str, venue: str) -> str:
    """Prefer the explicit postal town over a misleading Bonn fallback."""
    postal_city = re.search(r"\b\d{5}\s+([^,]+)", venue)
    if postal_city:
        city = postal_city.group(1).strip().split("-", 1)[0]
    else:
        city = rc.city_from_text(venue, rc.city_from_text(title, ""))
    return _CITY_ALIASES.get(city.casefold(), city)


def _events_from_listing(html: str, page_url: str) -> list:
    events = []
    blocks = re.split(
        r'(?=<div[^>]+id="markt\d+"[^>]+class="[^"]*\blisting\b)',
        html or "",
        flags=re.I,
    )
    for block in blocks:
        if not re.match(r'<div[^>]+id="markt\d+"', block, re.I):
            continue
        date_match = re.search(r"<mark\b[^>]*>(.*?)</mark>", block, re.S | re.I)
        title_match = re.search(r'<h3[^>]+class="[^"]*\bh2\b[^"]*"[^>]*>(.*?)</h3>', block, re.S | re.I)
        location_match = re.search(
            r'<img[^>]+marker-1\.svg[^>]*>\s*<span[^>]*>(.*?)</span>',
            block,
            re.S | re.I,
        )
        link_match = re.search(r'<a[^>]+href="([^"]*?/unsere-maerkte/[^"]+)"', block, re.I)
        if not (date_match and title_match and location_match):
            continue

        start = common.parse_date(rc.clean(date_match.group(1)))
        title = rc.clean(title_match.group(1))
        venue = rc.clean(location_match.group(1))
        city = _listing_city(title, venue)
        # This organizer publishes markets throughout NRW. Do not turn an
        # unknown/out-of-scope postal town into Bonn merely because it is not in
        # the regional gazetteer.
        resolved_coords, _, _ = common.resolve_location(city)
        if not resolved_coords:
            continue
        time_text = rc.time_text(rc.clean(block))
        link = rc.abs_url(_BASE_URL, link_match.group(1)) if link_match else page_url
        description = common.factual_event_description(
            title,
            date_value=start,
            time_text=time_text,
            venue=venue,
            city=city,
        )
        event = common.make_event(
            title,
            start,
            None,
            venue,
            city,
            description,
            link,
            "Grote & Hiller",
            "flohmarkt trödelmarkt second hand markt",
            0.94,
            time_text,
        )
        if event:
            events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    events = []
    for url in _URLS:
        try:
            parsed = _events_from_listing(common.fetch_url(url, timeout=20), url)
            common._record_endpoint(
                url,
                parser_type="html",
                parsed_event_count=len(parsed),
                parser_empty=not bool(parsed),
            )
            events.extend(parsed)
        except Exception as exc:
            common.log_source_error("Grote & Hiller", exc)
    return rc.dedupe(events)
