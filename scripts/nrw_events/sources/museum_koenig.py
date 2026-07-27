"""Museum Koenig Bonn — official LIB event calendar."""

import re
from datetime import datetime

from .. import common
from . import regional_common as rc

_URL = "https://bonn.leibniz-lib.de/de/veranstaltungen.html"
_SOURCE = "Museum Koenig Bonn"
_VENUE = "Museum Koenig Bonn"


def _card_blocks(html: str) -> list[str]:
    starts = [match.start() for match in re.finditer(
        r'<li\b[^>]*class="[^"]*\be-lib-event-calendar__list-item\b[^"]*"',
        html or "",
        re.I,
    )]
    return [html[start:end] for start, end in zip(starts, starts[1:] + [len(html)])]


def _detail_description(html: str, _event: dict) -> dict:
    main = re.search(r"<main\b[^>]*>(.*?)</main>", html or "", re.S | re.I)
    text = rc.clean(main.group(1) if main else "")
    limited_free_offer = bool(re.search(r"kostenlos\s+zzgl\.?\s+Eintritt\s+in\s+das\s+Museum", text, re.I))
    description = common.concise_description(text, max_chars=300 if limited_free_offer else 360)
    if limited_free_offer:
        description = (
            f"{description} Für das Angebot fällt kein zusätzliches Entgelt an; "
            "regulärer Museumseintritt ist erforderlich."
        )
    tiered_price = re.search(
        r"Kostenlos\s+für[^<]{0,100}Nichtmitglieder[^<]{0,100}(?:Euro|€)",
        html or "",
        re.I,
    )
    if tiered_price:
        description = f"{description} {rc.clean(tiered_price.group(0))}."
    meeting_point = re.search(
        r"<p>\s*Treffpunkt\s*</p>.*?e-list__item-text.*?<p>(.*?)</p>",
        html or "",
        re.S | re.I,
    )
    return {
        "description": description,
        "venue": rc.clean(meeting_point.group(1) if meeting_point else ""),
    }


def _merge_detail(event: dict, context: dict) -> dict:
    if (
        event.get("venue", "").casefold() == "externe veranstaltung"
        and context.get("venue")
    ):
        event["venue"] = context["venue"]
    return event


def events_from_html(html: str, detail_fetcher=None) -> list:
    events = []
    for card in _card_blocks(html):
        date_match = re.search(r'data-publication-date="(20\d{2}-\d{2}-\d{2})\s+(\d{2}):(\d{2}):\d{2}"', card, re.I)
        title_match = re.search(
            r'class="[^"]*\be-lib-event-calendar__list-item-title\b[^"]*"[^>]*>.*?'
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            card,
            re.S | re.I,
        )
        if not (date_match and title_match):
            continue

        start = datetime.strptime(
            f"{date_match.group(1)} {date_match.group(2)}:{date_match.group(3)}",
            "%Y-%m-%d %H:%M",
        )
        location_match = re.search(
            r'class="[^"]*\be-lib-event-calendar__date-location\b[^"]*"[^>]*>(.*?)</p>',
            card,
            re.S | re.I,
        )
        location_text = rc.clean(location_match.group(1) if location_match else "")
        location = location_text.rsplit(",", 1)[-1].strip() if "," in location_text else ""
        tags = [rc.clean(tag) for tag in re.findall(
            r'class="[^"]*\be-lib-cards__tag\b[^"]*"[^>]*>(.*?)</span>',
            card,
            re.S | re.I,
        )]
        free = bool(re.search(r'kostenfrei|kostenlos', card, re.I))
        description_parts = [tag for tag in tags if tag]
        if location:
            description_parts.append(f"Treffpunkt: {location}.")
        if free:
            description_parts.append("Das Angebot ist als kostenlos gekennzeichnet; Museumseintritt kann zusätzlich anfallen.")
        description = " ".join(description_parts) or "Veranstaltung im Museum Koenig Bonn."
        link = rc.abs_url(_URL, title_match.group(1))
        venue = "Externe Veranstaltung" if location.casefold() == "externe veranstaltung" else _VENUE
        event = common.make_event(
            rc.clean(title_match.group(2)),
            start,
            None,
            venue,
            "Bonn",
            description,
            link,
            _SOURCE,
            "Museum",
            1.0,
            start.strftime("%H:%M"),
            all_day=False,
        )
        if event:
            events.append(event)
    events = rc.dedupe_occurrences(events)
    if detail_fetcher:
        events = rc.enrich_descriptions(
            events,
            source=_SOURCE,
            cache_namespace="museum-koenig-bonn-v3",
            extract_context=_detail_description,
            fallback=lambda event: event.get("description", ""),
            detail_fetcher=detail_fetcher,
            needs_enrichment=lambda _event: True,
            merge_context=_merge_detail,
        )
        for event in events:
            if (
                "regulärer Museumseintritt ist erforderlich" in event["description"]
                or re.search(r"Nichtmitglieder.*(?:Euro|€)", event["description"], re.I)
            ):
                event["price"] = ""
            else:
                event["price"] = common.infer_free_admission_price(event["title"], event["description"])
    return events


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda html: events_from_html(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="museum-koenig-bonn-v3", timeout=20
            ),
        ),
        source_id="museum-koenig-bonn",
    )
