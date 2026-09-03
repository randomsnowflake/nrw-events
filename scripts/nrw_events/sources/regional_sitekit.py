"""SiteKit teaser calendars for municipal sources on the Sitepark CMS."""

import os
import re
import time
import urllib.parse
from functools import partial

from .. import common, components, richtext
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
    return rc.pagination_max(html)


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


def _fetch_calendar(city: str, source_id: str, url: str, trust: float) -> list:
    page_limit = {"value": 1}

    def stop_when(html: str, page: int) -> bool:
        if page == 1:
            page_limit["value"] = min(_pagination_max(html), _MAX_PAGES)
        return _page_starts_after_window(html) or page >= page_limit["value"]

    return rc.fetch_html_events(
        f"{_SOURCE} ({city})",
        url,
        lambda html: _events_from_teasers(html, url, city, trust, source_id),
        timeout=25,
        source_id=source_id,
        page_urls=_page_url,
        stop_when=stop_when,
        max_pages=_MAX_PAGES,
    )


def _merge_detail_context(event: dict, context: dict[str, str]) -> dict:
    """Replace generated copy, but keep richer prose already scraped from the listing."""
    description = context.get("description", "").strip()
    current = event.get("description", "").strip()
    should_replace = (
        description
        and (
            event.get("description_source") == "generated"
            or len(description) > len(current)
        )
    )
    if should_replace:
        event["description"] = description
        event["description_source"] = "scraped"
        event["description_html"] = context.get("description_html", "")
    else:
        context.pop("description", None)
        context.pop("description_html", None)
    return event


def fetch() -> list:
    events = components.run([
        components.Job(calendar[2], partial(_fetch_calendar, *calendar))
        for calendar in _CALENDARS
    ])
    events = rc.dedupe(events)
    for event in events:
        # A venue recovered for an empty teaser is display enrichment. Lock
        # that original blank so useful location facts never move an already
        # published event URL. Existing teaser venues keep normal identity.
        if not event.get("venue"):
            event["identity_venue"] = ""
            event["identity_venue_locked"] = True
    if components.enabled():
        # Deduplication still precedes enrichment. Keep one shared phase budget
        # and restore the original event order after independent hosts finish.
        deadline = time.monotonic() + max(0.0, min(120.0, float(os.environ.get("NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS", "120"))))
        groups = {}
        for index, event in enumerate(events):
            host = urllib.parse.urlsplit(event.get("link", "")).hostname or ""
            groups.setdefault(host, []).append((index, event))
        enriched = components.run([
            components.Job(f"https://{host}", partial(_enrich_group, rows, deadline))
            for host, rows in groups.items()
        ])
        for index, event in enriched:
            events[index] = event
    else:
        events = _enrich_details(events)
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
        event.update({
            "category_key": canonical["key"],
            "category_label": canonical["label"],
            "category_confidence": canonical.get("confidence", 0),
            "category_reason": canonical.get("reason", ""),
        })
    return _correct_categories(events)


def _enrich_group(indexed_events: list, deadline: float) -> list:
    rows = _enrich_details([event for _index, event in indexed_events], batch_timeout=deadline - time.monotonic())
    return [(indexed[0], row) for indexed, row in zip(indexed_events, rows, strict=True)]


def _enrich_details(events: list, *, batch_timeout: float = 120) -> list:
    return rc.enrich_descriptions(
        events,
        source=_SOURCE,
        cache_namespace="regional-sitekit-detail",
        extract_context=lambda html, event: _detail_context(
            html, event.get("city", ""), event.get("title", ""),
        ),
        fallback=lambda event: event.get("description", ""),
        needs_enrichment=lambda event: (
            event.get("description_source") == "generated"
            or not event.get("venue")
            or event.get("category_key") == "other"
        ),
        detail_fetcher=lambda url: common.fetch_detail_url(
            url,
            cache_namespace="regional-sitekit-detail",
            timeout=15,
            retry_attempts=1,
        ),
        merge_context=_merge_detail_context,
        # Six municipal calendars currently contribute roughly 140 in-window
        # detail pages. The shared 45-second default consistently stops inside
        # Brühl and starves every later municipality of structured venues.
        batch_timeout=batch_timeout,
    )


def _correct_categories(events: list) -> list:
    """Apply reviewed SiteKit format signals that generic hints cannot express."""
    for event in events:
        title = event.get("title", "")
        description = event.get("description", "")
        key = ""
        if re.search(r"\bkinotag\b", title, re.I):
            key = "cinema"
        elif title.casefold().startswith("adfc:") and re.search(
            r"\b(?:rundtour|radtour|strecke|\d+\s*km)\b", description, re.I
        ):
            key = "outdoor"
        elif re.search(r"\bgig\b", title, re.I) and re.search(
            r"\b(?:blues|jazz|rock|musik|konzert)\b", f"{title} {description}", re.I
        ):
            key = "concert"
        # A reviewed source-format signal may promote an agreeing generic
        # classification to deterministic confidence, but it must not replace
        # a different, stronger category selected from the actual event topic.
        if not key or event.get("category_key") not in {"other", key}:
            continue
        category = common.category_taxonomy.CATEGORY_BY_KEY[key]
        event.update({
            "category_key": category["key"],
            "category_label": category["label"],
            "category_confidence": 1.0,
            "category_reason": f"source-format:{key}",
        })
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
        r'<div[^>]+class="SP-Paragraph"[^>]*>(.*?)</div>', html or "", re.S | re.I,
    )
    return richtext.sanitize_rich_text("".join(fragments))


def _visible_venue_context(html: str) -> dict[str, str]:
    """Read SiteKit's visible venue section when its JSON-LD Place is empty."""
    parser = rc.ClassScopedTextParser({
        "venue": lambda _tag, attrs: (
            (attrs.get("aria-labelledby") or "").casefold() == "veranstaltungsort"
        ),
    })
    parser.feed(html or "")
    lines = [
        rc.clean(line)
        for line in parser.block_text("venue").splitlines()
        if rc.clean(line)
    ]
    if lines and lines[0].casefold() == "veranstaltungsort":
        lines.pop(0)
    if "Kontakt" in lines:
        lines = lines[:lines.index("Kontakt")]
    if not lines:
        return {}

    if len(lines) == 1:
        parts = [part.strip() for part in lines[0].split(",") if part.strip()]
        if len(parts) >= 3:
            return {"venue": parts[0], "venue_address": ", ".join(parts[1:])}
        return {"venue": lines[0]}
    return {"venue": lines[0], "venue_address": ", ".join(lines[1:])}


def _detail_context(
    html: str, city_name: str = "", event_title: str = "",
) -> dict[str, str]:
    """Return the SiteKit detail copy and its schema.org place fields."""
    context = {
        "description": _detail_description(html),
        "description_html": _detail_rich_text(html),
    }
    items = common.jsonld_event_items(html)
    location = items[0].get("location") if items else None
    if isinstance(location, list):
        location = location[0] if location else None
    if isinstance(location, dict):
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

    for key, value in _visible_venue_context(html).items():
        context.setdefault(key, value)

    # This municipal calendar uses the named attraction as the title prefix
    # while omitting its Place object. Keep the inference deliberately narrow.
    if not context.get("venue"):
        named_attraction = re.match(r"^(Kletterwald\s+[^:]+):", event_title, re.I)
        if named_attraction:
            context["venue"] = rc.clean(named_attraction.group(1))

    # Some SiteKit calendars publish an empty JSON-LD location while their
    # visible body names a precise, labelled meeting point. Structured data
    # remains authoritative; prose only fills fields it left blank.
    prose_place = rc.explicit_place_context(context["description"], city_name)
    for key, value in prose_place.items():
        context.setdefault(key, value)
    return context


def _events_from_teasers(html: str, base: str, city: str, trust: float,
                         source_id: str) -> list:
    events = []
    for block in rc.class_tag_blocks(html, "article", "SP-Teaser"):
        href = rc.attribute_from_class_tag(block, "a", "SP-Teaser__inner", "href")
        date = re.search(r'<span class="SP-Scheduling__date">([^<]+)', block, re.S | re.I)
        title = re.search(r'<h4 class="SP-Teaser__headline">(.*?)</h4>', block, re.S | re.I)
        desc = re.search(r'<div class="SP-Teaser__abstract">(.*?)</div>', block, re.S | re.I)
        if not (date and title):
            continue
        text = rc.clean(block)
        time_text = rc.time_text(text)
        # SiteKit uses midnight when editors leave the time empty. Publishing
        # it as a real start time misleads visitors; retain the date as all-day.
        if time_text == "00:00":
            time_text = ""
        start = rc.with_time(rc.parse_dt(date.group(1)), time_text)
        description = rc.clean(desc.group(1) if desc else "")
        ev = common.make_event(
            rc.clean(title.group(1)),
            start,
            start,
            city,
            city,
            description,
            rc.abs_url(base, href),
            _SOURCE,
            "kommunal kultur markt ausstellung konzert führung",
            trust,
            time_text,
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
                separator = (
                    " " if not description or description.endswith((".", "!", "?"))
                    else ". "
                )
                ev["description"] = common.GeneratedDescription(
                    f"{description}{separator}{fallback}".strip()
                )
                ev["description_source"] = "generated"
            events.append(ev)
    return events
