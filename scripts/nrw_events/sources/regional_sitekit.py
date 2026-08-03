"""SiteKit teaser calendars for municipal sources on the Sitepark CMS."""

import re
import urllib.parse

from .. import common, richtext
from . import regional_common as rc

_SOURCE = "SiteKit regional"
_MAX_PAGES = 30
_CALENDARS = [
    ("Brühl", "sitekit-bruehl", "https://www.bruehl.de/tksf/veranstaltungskalender/veranstaltungskalender.php", 0.9),
    ("Wesseling", "sitekit-wesseling", "https://www.wesseling.de/kultur-sport/veranstaltungskalender.php", 0.86),
    ("Frechen", "sitekit-frechen", "https://www.stadt-frechen.de/veranstaltungskalender", 0.9),
    ("Hürth", "sitekit-huerth", "https://www.huerth.de/veranstaltungskalender.php", 0.9),
    ("Erftstadt", "sitekit-erftstadt", "https://www.erftstadt.de/aktuelles/terminkalender.php", 0.9),
    ("Zülpich", "sitekit-zuelpich", "https://www.zuelpich.de/kultur-sport/veranstaltungskalender.php", 0.88),
]


def _pagination_max(html: str) -> int:
    match = re.search(r"(?:&quot;|\")max(?:&quot;|\")\s*:\s*(\d+)", html or "")
    return max(1, int(match.group(1))) if match else 1


def _page_url(url: str, page: int) -> str:
    if page <= 1:
        return url
    separator = "&" if "?" in url else "?"
    query = urllib.parse.urlencode({"sp:page[eventSearch-1.form][0]": page})
    return f"{url}{separator}{query}"


def _page_starts_after_window(html: str) -> bool:
    dates = [
        rc.parse_dt(value)
        for value in re.findall(
            r'class="SP-Scheduling__date"[^>]*>([^<]+)',
            html or "",
            re.I,
        )
    ]
    dates = [value for value in dates if value]
    return bool(dates) and min(dates) > common.END_DATE


def _parse_page(html: str, endpoint: str, city: str, source_id: str, base_url: str, trust: float) -> list:
    with common.capture_parser_metrics() as metrics:
        events = _events_from_teasers(html, base_url, city, trust, source_id)
    parser_empty = not events and metrics["out_of_window_count"] == 0
    common._record_endpoint(
        endpoint,
        parser_type="html",
        candidate_count=metrics["candidate_count"],
        out_of_window_count=metrics["out_of_window_count"],
        parsed_event_count=len(events),
        parser_empty=parser_empty,
    )
    if parser_empty:
        common.log_source_error(
            f"{_SOURCE} ({city})",
            rc.ParserEmptyError("parser returned no event records"),
            source_id=source_id,
        )
    return events


def _fetch_calendar(city: str, source_id: str, url: str, trust: float) -> list:
    events = []
    try:
        first = common.fetch_url(url, timeout=25)
        events.extend(_parse_page(first, url, city, source_id, url, trust))
    except Exception as exc:
        common.log_source_error(f"{_SOURCE} ({city})", exc, source_id=source_id)
        return []

    if _page_starts_after_window(first):
        return events

    max_page = min(_pagination_max(first), _MAX_PAGES)
    for page in range(2, max_page + 1):
        endpoint = _page_url(url, page)
        try:
            html = common.fetch_url(endpoint, timeout=25)
            events.extend(
                _parse_page(
                    html,
                    endpoint,
                    city,
                    source_id,
                    url,
                    trust,
                )
            )
            if _page_starts_after_window(html):
                break
        except Exception as exc:
            common.log_source_error(
                f"{_SOURCE} ({city}) page {page}",
                exc,
                source_id=source_id,
            )
    return events


def fetch() -> list:
    events = []
    for calendar in _CALENDARS:
        events.extend(_fetch_calendar(*calendar))
    events = rc.dedupe(events)
    events = rc.enrich_descriptions(
        events,
        source=_SOURCE,
        cache_namespace="regional-sitekit-detail",
        extract_context=lambda html, _event: _detail_context(html),
        fallback=lambda event: event.get("description", ""),
        needs_enrichment=lambda event: (not event.get("venue") or event.get("category_key") == "other"),
    )
    for event in events:
        if event.get("category_key") != "other":
            continue
        canonical = common.category_taxonomy.categorize_event(
            event.get("category", ""),
            event.get("title", ""),
            event.get("description", ""),
            venue=event.get("venue", ""),
            source=event.get("source", ""),
        )
        event.update(
            {
                "category_key": canonical["key"],
                "category_label": canonical["label"],
                "category_confidence": canonical.get("confidence", 0),
                "category_reason": canonical.get("reason", ""),
            }
        )
    return events


def _detail_description(html: str) -> str:
    """Prefer visible SiteKit body copy over a teaser synopsis.

    Only the bare `SP-Paragraph` block is body copy. SiteKit reuses the class
    with a modifier for the town-hall footer (`--footer`: postal address, phone,
    Bürgeramt opening hours), the venue contact card (`SP-Contact__…`) and the
    cookie banner, all of which used to land in the description and drown the
    two sentences that actually describe the event.
    """
    paragraphs = [
        rc.clean_blocks(fragment)
        for fragment in re.findall(
            r'<div[^>]+class="SP-Paragraph"[^>]*>(.*?)</div>',
            html or "",
            re.S | re.I,
        )
    ]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph]
    return common.concise_description("\n\n".join(paragraphs))


def _detail_rich_text(html: str) -> str:
    """The same body copy with its headings, lists and emphasis intact."""
    fragments = re.findall(
        r'<div[^>]+class="SP-Paragraph"[^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    return richtext.sanitize_rich_text("".join(fragments))


def _detail_context(html: str) -> dict[str, str]:
    """Return the SiteKit detail copy and its schema.org place fields."""
    context = {
        "description": _detail_description(html),
        "description_html": _detail_rich_text(html),
    }
    items = common.jsonld_event_items(html)
    location = items[0].get("location") if items else None
    if isinstance(location, list):
        location = location[0] if location else None
    if not isinstance(location, dict):
        return context

    venue = rc.clean(str(location.get("name") or ""))
    address = location.get("address")
    address_parts = []
    if isinstance(address, dict):
        street = rc.clean(str(address.get("streetAddress") or ""))
        postcode = rc.clean(str(address.get("postalCode") or ""))
        city = rc.clean(str(address.get("addressLocality") or ""))
        locality = " ".join(part for part in (postcode, city) if part)
        address_parts = [part for part in (street, locality) if part]
    elif isinstance(address, str):
        address_parts = [rc.clean(address)]

    if venue:
        context["venue"] = venue
    if address_parts:
        context["venue_address"] = ", ".join(address_parts)
    return context


def _events_from_teasers(html: str, base: str, city: str, trust: float, source_id: str) -> list:
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
        description = rc.clean(desc.group(1) if desc else "")
        ev = common.make_event(
            rc.clean(title.group(1)),
            start,
            start,
            city,
            city,
            description,
            rc.abs_url(base, href.group(1) if href else ""),
            _SOURCE,
            "kommunal kultur markt ausstellung konzert führung",
            trust,
            rc.time_text(text),
            source_id=source_id,
        )
        if ev:
            if len(description) < 40:
                fallback = common.factual_event_description(
                    ev["title"],
                    date_value=start,
                    time_text=ev.get("time", ""),
                    venue=city,
                    city=city,
                    calendar_name=city,
                )
                separator = " " if not description or description.endswith((".", "!", "?")) else ". "
                ev["description"] = common.GeneratedDescription(f"{description}{separator}{fallback}".strip())
                ev["description_source"] = "generated"
            events.append(ev)
    return events
