"""Enrich source records from their public event detail pages.

Listings and feeds are discovery transports.  They frequently expose only a
teaser even though the linked first-party page contains the actual event copy,
admission, registration notes and address.  This module is the shared second
pass: every registered source benefits without duplicating HTTP/cache policy in
each adapter, while source-specific extractors can still handle unusual markup.

Only high-confidence event containers are accepted.  A generic ``main`` or
``article`` is deliberately not scraped because navigation and related-content
text is worse than an honest short description.
"""

from __future__ import annotations

import os
import re
import time
from collections import Counter
from functools import partial
from typing import Literal
from urllib.parse import urldefrag, urlsplit

from . import common, components, http, richtext
from .detail_extractors import extract_source_context, source_price, supports_repeated_detail, template_price
from .detail_parsing import (
    _best_description,
    _exact_jsonld_description,
    _first,
    _jsonld_candidates,
    _product_meta_price,
    _SemanticHTML,
    _single_prose_time_range,
    _timestamp_with_clock,
    _timestamp_with_timezone,
    _tribe_price,
    _visible_labeled_value,
)
from .detail_types import DetailContext
from .models import RawEvent

_NON_DOCUMENT_SUFFIXES = (
    ".css", ".csv", ".gif", ".ics", ".jpeg", ".jpg", ".json", ".pdf",
    ".png", ".svg", ".webp", ".xml", ".zip",
)
_SKIPPED_HOSTS = {
    "example.com", "example.org", "example.test", "kihapp.com", "localhost",
    "www.example.com", "www.example.org", "www.kihapp.com",
}
_GENERIC_CACHE_NAMESPACE = "universal-event-details-v2"


def enabled() -> bool:
    """Whether the shared detail pass is enabled (on by default)."""
    return os.environ.get("NRW_EVENTS_DETAIL_ENRICHMENT", "1").strip().casefold() not in {
        "0", "false", "no", "off",
    }


def _candidate_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold().rstrip("/")
    return bool(
        parsed.scheme in {"http", "https"}
        and host
        and host not in _SKIPPED_HOSTS
        and not path.endswith(_NON_DOCUMENT_SUFFIXES)
    )


def _needs_detail(event: RawEvent) -> bool:
    """Limit the expensive second pass to genuinely incomplete teasers."""
    if event.get("_detail_page_enriched") is True:
        return False
    description = richtext.to_plain_text(str(
        event.get("description_html") or event.get("description") or ""
    )).strip()
    missing_decision_facts = sum((
        not str(event.get("organizer") or "").strip(),
        not str(event.get("venue_address") or "").strip(),
        not str(event.get("price") or "").strip() and not isinstance(event.get("admission"), dict),
        bool(event.get("all_day", not event.get("time"))) and not str(event.get("time") or event.get("start_at") or "").strip(),
    ))
    return (
        len(description) < 240
        or _invalid_short_venue(str(event.get("venue") or ""))
        or missing_decision_facts >= 2
    )


def _invalid_short_venue(value: str) -> bool:
    """Treat empty and one-character venue fragments as missing source data."""
    return len(re.sub(r"[^a-z0-9]+", "", common.clean_html(value).casefold())) <= 1


def _master_data_only(event: RawEvent) -> bool:
    source_id = str(event.get("source_id") or "").casefold()
    source = str(event.get("source") or "").casefold()
    return (
        source_id == "ruhr-guide" or source_id == "meetup"
        or source_id.startswith("meetup-") or source in {"meetup", "ruhr-guide"}
    )


def extract_detail_context(document: str, event: RawEvent) -> DetailContext:
    """Extract richer, auditable fields from one event detail document."""
    source_context = extract_source_context(document, event)
    if source_context is not None:
        return source_context
    parser = _SemanticHTML()
    parser.feed(document or "")
    description, description_html = _best_description(
        document or "", parser, str(event.get("title") or ""),
    )
    exact_description, exact_description_html = _exact_jsonld_description(document, event)
    tribe_price = _tribe_price(document)
    arp_subline_price = source_price(document, event)
    context: DetailContext = {
        "description": description,
        "description_html": description_html,
        "exact_description": exact_description,
        "exact_description_html": exact_description_html,
        "price": arp_subline_price or _product_meta_price(parser) or _first(parser.item_values, "price") or _visible_labeled_value(
            document, "Preis", "Preise", "Kosten", "Eintritt",
        ) or template_price(document),
        # A bare itemprop=name may be the event title, organizer or venue.  It
        # is only promoted below when JSON-LD proves it belongs to location.
        "venue": "",
        "venue_address": " ".join(filter(None, (
            _first(parser.item_values, "streetaddress"),
            _first(parser.item_values, "postalcode"),
            _first(parser.item_values, "addresslocality"),
        ))) or _visible_labeled_value(document, "Adresse", "Anschrift"),
        "organizer": "",
        "time": "",
        "start_at": "",
        "end_at": "",
    }
    for item in _jsonld_candidates(document or "", str(event.get("title") or ""))[:1]:
        structured_price = common._jsonld_admission_price(item)
        # A calendar's visitor-facing event cost is stronger evidence than its
        # generated JSON-LD. Plugins can retain a default currency there even
        # while rendering the organizer's complete price correctly.
        if structured_price is not None and not tribe_price and not arp_subline_price:
            context["price"] = structured_price
        location = item.get("location")
        if isinstance(location, list):
            location = next((value for value in location if isinstance(value, dict)), None)
        if isinstance(location, dict):
            context["venue"] = common.clean_html(str(location.get("name") or "")) or context["venue"]
            address = location.get("address")
            if isinstance(address, dict):
                address_parts: list[str] = []
                for key in ("streetAddress", "postalCode", "addressLocality"):
                    part = common.clean_html(str(address.get(key) or ""))
                    current = " ".join(address_parts).casefold()
                    if part and part.casefold() not in current:
                        address_parts.append(part)
                structured_address = " ".join(address_parts)
                context["venue_address"] = structured_address or context["venue_address"]
        organizer = item.get("organizer")
        if isinstance(organizer, list):
            organizer = next((value for value in organizer if isinstance(value, dict | str)), "")
        if isinstance(organizer, dict):
            organizer = organizer.get("name") or ""
        if isinstance(organizer, str):
            context["organizer"] = common.clean_html(organizer)[:300]
        start_at = str(item.get("startDate") or "").strip()
        end_at = str(item.get("endDate") or "").strip()
        event_date = str(event.get("start_date") or event.get("date") or "")[:10]
        if start_at[:10] == event_date:
            def structured_clock(value: str) -> str:
                match = re.match(r"^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})", value)
                return match.group(1) if match else ""

            start_clock = structured_clock(start_at)
            end_clock = structured_clock(end_at)
            if start_clock:
                timezone_name = str(event.get("timezone") or "Europe/Berlin")
                context["time"] = (
                    f"{start_clock}–{end_clock}"
                    if end_clock and end_clock != start_clock else start_clock
                )
                context["start_at"] = _timestamp_with_timezone(
                    start_at, timezone_name,
                )
                if end_at:
                    context["end_at"] = _timestamp_with_timezone(
                        end_at, timezone_name,
                    )
    # Exact event copy may correct stale plugin-generated JSON-LD. Without an
    # exact structured match, visible prose may fill a missing schedule but
    # must not override one that the page already structured explicitly.
    schedule_copy = context["exact_description"] or (
        context["description"] if not context["time"] else ""
    )
    prose_schedule = _single_prose_time_range(schedule_copy)
    if prose_schedule:
        start_clock, end_clock = prose_schedule
        context["time"] = f"{start_clock}–{end_clock}"
        event_date = str(event.get("start_date") or event.get("date") or "")[:10]
        end_date = str(event.get("end_date") or event_date)[:10]
        timezone_name = str(event.get("timezone") or "Europe/Berlin")
        context["start_at"] = _timestamp_with_clock(
            context["start_at"], event_date, start_clock, timezone_name,
        )
        context["end_at"] = _timestamp_with_clock(
            context["end_at"], end_date, end_clock,
            timezone_name,
        )
    if _master_data_only(event):
        context["description"] = ""
        context["description_html"] = ""
    return context


def _richer(candidate: str, current: str) -> bool:
    candidate_text = common.clean_html(candidate)
    current_text = common.clean_html(current)
    return bool(candidate_text and len(candidate_text) >= len(current_text) + max(40, len(current_text) // 5))


def apply_detail_context(event: RawEvent, context: DetailContext) -> RawEvent:
    """Merge only facts that improve the source record."""
    enriched = event.copy()
    exact_description = context.get("exact_description", "")
    replaces_generated = bool(
        exact_description and event.get("description_source") == "generated"
    )
    if replaces_generated or _richer(
        context.get("description", ""), str(event.get("description") or ""),
    ):
        # Plain text is duplicated into planner/search payloads; keep a long,
        # sentence-safe searchable excerpt there while description_html retains
        # the complete sanitized event document for the detail page.
        replacement = exact_description if replaces_generated else context["description"]
        replacement_html = (
            context.get("exact_description_html", "")
            if replaces_generated else context.get("description_html", "")
        )
        enriched["description"] = common.concise_description(
            replacement, max_chars=8000,
        )
        enriched["description_html"] = replacement_html
        enriched["description_source"] = "scraped"
        # Listing teasers may have been classified before this stronger detail
        # evidence existed. Reopen only inferred decisions; explicit adapter
        # locks remain authoritative at the canonical boundary.
        if not str(event.get("category_reason") or "").startswith("source:locked-default:"):
            enriched.pop("category_key", None)
            enriched.pop("category_label", None)
            enriched.pop("category_confidence", None)
            enriched.pop("category_reason", None)
    elif (
        context.get("description_html")
        and richtext.text_length(context["description_html"]) >= richtext.text_length(str(event.get("description_html") or ""))
        and richtext.describes_same_copy(context["description_html"], str(event.get("description") or ""))
    ):
        enriched["description_html"] = context["description_html"]

    price = context.get("price", "")
    if price and (
        not str(enriched.get("price") or "").strip()
        or enriched.get("admission_basis") not in {"explicit", "structured"}
    ):
        enriched["price"] = common.clean_html(price)[:160]
        enriched["admission_basis"] = "explicit"
    fields: tuple[Literal["venue", "venue_address"], ...] = ("venue", "venue_address")
    for field in fields:
        current = str(enriched.get(field) or "").strip()
        candidate = str(context.get(field) or "").strip()
        if candidate and (not current or (field == "venue" and _invalid_short_venue(current))):
            enriched[field] = candidate
        elif field == "venue_address" and candidate:
            words = current.split()
            if (
                len(words) >= 2
                and words[-1].casefold() == words[-2].casefold()
                and not candidate.casefold().endswith(
                    f"{words[-1].casefold()} {words[-1].casefold()}"
                )
            ):
                enriched[field] = candidate
    organizer = str(context.get("organizer") or "").strip()
    if organizer and not str(enriched.get("organizer") or "").strip():
        enriched["organizer"] = organizer
    time_value = str(context.get("time") or "").strip()
    if (
        time_value
        and not str(enriched.get("time") or enriched.get("start_at") or "").strip()
        and not enriched.get("identity_time_locked")
    ):
        enriched["time"] = time_value
        enriched["all_day"] = False
        if context.get("start_at"):
            enriched["start_at"] = context["start_at"]
        if context.get("end_at"):
            enriched["end_at"] = context["end_at"]
    return enriched


def enrich_events(events: list[RawEvent], *, cache_namespace: str = _GENERIC_CACHE_NAMESPACE,
                  parallel_components: bool = False) -> list[RawEvent]:
    """Enrich unique public detail links, failing soft per event.

    A URL shared by several events is normally an overview or rolling article;
    treating it as one event's detail page causes cross-card contamination.
    """
    if not enabled():
        return events
    batch_timeout = float(os.environ.get("NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS", "45"))
    deadline = time.monotonic() + max(batch_timeout, 0.0)
    eligible_ids: set[int] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            if common.event_in_window(event) and _needs_detail(event):
                eligible_ids.add(id(event))
        except (AttributeError, TypeError, ValueError):
            # Structurally invalid records are rejected by canonical validation;
            # they must not trigger network requests first.
            continue
    link_counts = Counter(
        urldefrag(str(event.get("link") or ""))[0]
        for event in events
        if isinstance(event, dict) and id(event) in eligible_ids
    )
    if parallel_components and components.enabled():
        groups: dict[str, list[tuple[int, RawEvent]]] = {}
        for index, event in enumerate(events):
            link = str(event.get("link") or "") if isinstance(event, dict) else ""
            host = (urlsplit(link).hostname or "") if id(event) in eligible_ids else ""
            groups.setdefault(host, []).append((index, event))
        rows = components.run([
            components.Job(f"https://{host}", partial(
                _enrich_indexed, indexed, cache_namespace, deadline, eligible_ids, link_counts,
            )) for host, indexed in groups.items()
        ])
        result = list(events)
        for index, event in rows:
            result[index] = event
        return result
    return _enrich_batch(events, cache_namespace, deadline, eligible_ids, link_counts)


def _enrich_indexed(indexed: list[tuple[int, RawEvent]], cache_namespace: str, deadline: float,
                    eligible_ids: set[int], link_counts: Counter[str]) -> list[tuple[int, RawEvent]]:
    rows = _enrich_batch([event for _index, event in indexed], cache_namespace, deadline, eligible_ids, link_counts)
    return [(pair[0], row) for pair, row in zip(indexed, rows, strict=True)]


def _enrich_batch(events: list[RawEvent], cache_namespace: str, deadline: float,
                  eligible_ids: set[int], link_counts: Counter[str]) -> list[RawEvent]:
    documents: dict[str, str] = {}
    enriched: list[RawEvent] = []
    for event in events:
        if not isinstance(event, dict):
            enriched.append(event)
            continue
        if id(event) not in eligible_ids:
            enriched.append(event)
            continue
        remaining = deadline - time.monotonic()
        # A detail page may be retried up to three times. Do not start work
        # that cannot finish within this source's optional enrichment budget.
        if remaining < 3.0:
            result = getattr(common._SOURCE_CONTEXT, "result", None)
            if result is not None:
                result.detail_deadline_skipped_event_count += 1
            enriched.append(event)
            continue
        link = str(event.get("link") or "")
        fetch_link = urldefrag(link)[0]
        if (
            (link_counts[fetch_link] != 1 and not supports_repeated_detail(event))
            or not _candidate_url(fetch_link)
        ):
            enriched.append(event)
            continue
        try:
            if fetch_link not in documents:
                hostname = (urlsplit(fetch_link).hostname or "").casefold()
                with http._optional_detail_request(fetch_link):
                    documents[fetch_link] = common.fetch_detail_url(
                        fetch_link,
                        cache_namespace=cache_namespace,
                        timeout=min(20.0, remaining / 3.0),
                        brightdata_fallback=True,
                        allowed_hosts=(hostname,),
                        cache_failures=True,
                    )
            document = documents[fetch_link]
            enriched.append(apply_detail_context(event, extract_detail_context(document, event)))
        except Exception as exc:
            common.log_source_error(f"{event.get('source') or 'event'} detail", exc, error_type="OptionalDetailWarning")
            enriched.append(event)
    return enriched
