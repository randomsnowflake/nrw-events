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

from collections import Counter
from html import escape
from html.parser import HTMLParser
import os
import re
from urllib.parse import urlsplit

from . import common, richtext


_NON_DOCUMENT_SUFFIXES = (
    ".css", ".csv", ".gif", ".ics", ".jpeg", ".jpg", ".json", ".pdf",
    ".png", ".svg", ".webp", ".xml", ".zip",
)
_SKIPPED_HOSTS = {
    "example.com", "example.org", "example.test", "localhost",
    "www.example.com", "www.example.org",
}
_CONTENT_TOKENS = {
    "article-content", "content-detail", "detail-content", "entry-content",
    "event-content", "event-description", "event-details", "event-text",
    "eventdetail", "eventdescription", "events_page_detail",
    "rich-text", "shapehub-detail-description", "tx-gbevents-pi1", "va-content",
    "veranstaltungsbeschreibung", "veranstaltungsdetails",
}
_GENERIC_CACHE_NAMESPACE = "universal-event-details-v2"
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


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


def _attributes(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name.casefold(): value or "" for name, value in attrs}


def _attribute_tokens(attrs: dict[str, str]) -> set[str]:
    return {
        token.casefold()
        for value in (attrs.get("class", ""), attrs.get("id", ""))
        for token in re.split(r"[^a-zA-Z0-9_-]+", value)
        if token
    }


def _is_event_type(value: str) -> bool:
    return bool(re.search(r"(?:schema.org/)?[A-Za-z]*Event\b", value or "", re.I))


class _SemanticHTML(HTMLParser):
    """Collect high-confidence event fragments and machine-readable values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.captures: list[dict[str, object]] = []
        self.active: list[dict[str, object]] = []
        self.meta: dict[str, str] = {}
        self.item_values: dict[str, list[str]] = {}
        self._item_stack: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag, attrs):
        attr = _attributes(attrs)
        tokens = _attribute_tokens(attr)
        itemprop = attr.get("itemprop", "").casefold()
        for capture in self.active:
            capture["parts"].append(self.get_starttag_text() or f"<{tag}>")
            if tag not in _VOID_TAGS:
                capture["depth"] = int(capture["depth"]) + 1

        # HTMLParser does not emit an end tag for HTML void elements.  Treating
        # them like containers makes a description meta tag or image swallow
        # the rest of the document and also corrupts every outer depth count.
        if tag in _VOID_TAGS:
            if itemprop and attr.get("content"):
                self.item_values.setdefault(itemprop, []).append(attr["content"])
            if tag == "meta":
                key = (attr.get("property") or attr.get("name") or "").casefold()
                if key and attr.get("content"):
                    self.meta[key] = attr["content"]
            return

        score = 0
        if itemprop in {"description", "articlebody"}:
            score = 100
        elif tokens & _CONTENT_TOKENS:
            score = 80
        elif _is_event_type(attr.get("itemtype", "")) and tag in {"main", "article", "section", "div"}:
            score = 70
        if score:
            capture = {"tag": tag, "depth": 1, "score": score, "parts": []}
            self.captures.append(capture)
            self.active.append(capture)
            capture["parts"].append(self.get_starttag_text() or f"<{tag}>")

        if itemprop:
            content = attr.get("content", "")
            if content:
                self.item_values.setdefault(itemprop, []).append(content)
            self._item_stack.append((itemprop, []))
        else:
            self._item_stack.append(("", []))

        if tag == "meta":
            key = (attr.get("property") or attr.get("name") or "").casefold()
            if key and attr.get("content"):
                self.meta[key] = attr["content"]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        for capture in self.active:
            capture["parts"].append(escape(data, quote=False))
        for itemprop, parts in self._item_stack:
            if itemprop:
                parts.append(data)

    def handle_endtag(self, tag):
        for capture in list(self.active):
            capture["parts"].append(f"</{tag}>")
            capture["depth"] = int(capture["depth"]) - 1
            if capture["depth"] == 0:
                self.active.remove(capture)
        if self._item_stack:
            itemprop, parts = self._item_stack.pop()
            if itemprop:
                value = common.clean_html(" ".join(parts))
                if value:
                    self.item_values.setdefault(itemprop, []).append(value)


def _jsonld_candidates(document: str, title: str) -> list[dict]:
    candidates = common.jsonld_event_items(document)
    if not candidates:
        return []
    title_key = re.sub(r"[^a-z0-9]+", "", title.casefold())

    def similarity(item: dict) -> tuple[int, int]:
        item_key = re.sub(r"[^a-z0-9]+", "", common.clean_html(str(item.get("name") or "")).casefold())
        shared = os.path.commonprefix([title_key, item_key])
        return (len(shared), len(str(item.get("description") or "")))

    return sorted((item for item in candidates if isinstance(item, dict)), key=similarity, reverse=True)


def _best_description(document: str, parser: _SemanticHTML, title: str) -> tuple[str, str]:
    choices: list[tuple[int, int, str]] = []
    for capture in parser.captures:
        fragment = "".join(capture["parts"])
        if re.search(r'class=["\'][^"\']*\bva-content\b', fragment, re.I):
            # Arp Museum nests its share/calendar controls inside va-content.
            # They are page furniture, while the preceding paragraphs are the
            # complete editorial event copy.
            fragment = re.split(
                r'<div[^>]+class=["\'][^"\']*\bva-content-cta\b',
                fragment,
                maxsplit=1,
                flags=re.I,
            )[0]
            fragment = re.sub(r"<figure\b.*?</figure>", "", fragment, flags=re.I | re.S)
        sanitized = richtext.sanitize_rich_text(fragment)
        plain = richtext.to_plain_text(sanitized)
        if plain and common.clean_html(title).casefold() != plain.casefold():
            choices.append((int(capture["score"]), len(plain), sanitized))
    for item in _jsonld_candidates(document, title):
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            sanitized = richtext.sanitize_rich_text(description)
            choices.append((65, richtext.text_length(sanitized), sanitized))
    meta_description = parser.meta.get("og:description") or parser.meta.get("description") or ""
    if meta_description:
        sanitized = richtext.from_plain_text(common.clean_html(meta_description))
        choices.append((25, richtext.text_length(sanitized), sanitized))
    if not choices:
        return "", ""
    # Confidence wins before length: a huge event-root container must not beat
    # an explicit itemprop=description merely by including page furniture.
    _, _, html = max(choices, key=lambda choice: (choice[0], choice[1]))
    html = _append_supplemental_details(document, html)
    return richtext.to_plain_text(html), html


def _append_supplemental_details(document: str, description_html: str) -> str:
    """Keep event facts that municipal templates place beside the prose.

    Köln's official detail pages are the first concrete contract: registration
    and age are siblings of ``itemprop=description``, not children of it.  The
    patterns are intentionally label-bound and therefore cannot absorb generic
    navigation or contact furniture.
    """
    additions: list[str] = []
    for heading, pattern in (
        ("Hinweis", r'<span[^>]+itemprop=["\']age["\'][^>]*>(.*?)</span>'),
        ("Anmeldung", r'<strong>\s*Anmeldung:\s*</strong>.*?<span[^>]*>(.*?)</span>'),
    ):
        match = re.search(pattern, document or "", re.I | re.S)
        value = common.clean_html(match.group(1)) if match else ""
        if value and value.casefold() not in richtext.to_plain_text(description_html).casefold():
            additions.append(f"<h3>{heading}</h3><p>{escape(value, quote=False)}</p>")
    return description_html + "".join(additions)


def _first(values: dict[str, list[str]], *names: str) -> str:
    for name in names:
        for value in values.get(name.casefold(), []):
            cleaned = common.clean_html(value)
            if cleaned:
                return cleaned
    return ""


def _visible_labeled_value(document: str, *labels: str) -> str:
    """Read a short value following an explicit, visible field label.

    Several otherwise well-structured calendars omit schema.org admission and
    address fields.  Label-bound extraction keeps this conservative: arbitrary
    currency-like page text (for example vendor fees or related events) is not
    promoted.
    """
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"<(?:b|strong)[^>]*>\s*(?:{label_pattern})\s*:?\s*</(?:b|strong)>"
        rf"\s*(?:<br\s*/?>\s*)?(.*?)(?=<br\s*/?>|</p>|</div>|</li>|</td>)",
        document or "",
        re.I | re.S,
    )
    return common.clean_html(match.group(1))[:240] if match else ""


def _template_price(document: str) -> str:
    """Extract a price from a known event-only field without broad guessing."""
    if "MyEventButton" in (document or "") and "springmaus-theater.de" in (document or ""):
        for value in re.findall(r'<div[^>]+class=["\']mb-4["\'][^>]*>([^<]+)</div>', document, re.I):
            cleaned = common.clean_html(value)
            if re.search(r"(?:€|\bEUR\b|\bEuro\b)", cleaned, re.I):
                return cleaned[:240]
    return ""


def extract_detail_context(document: str, event: dict) -> dict[str, str]:
    """Extract richer, auditable fields from one event detail document."""
    parser = _SemanticHTML()
    parser.feed(document or "")
    description, description_html = _best_description(
        document or "", parser, str(event.get("title") or ""),
    )
    context = {
        "description": description,
        "description_html": description_html,
        "price": _first(parser.item_values, "price") or _visible_labeled_value(
            document, "Preis", "Preise", "Kosten", "Eintritt",
        ) or _template_price(document),
        # A bare itemprop=name may be the event title, organizer or venue.  It
        # is only promoted below when JSON-LD proves it belongs to location.
        "venue": "",
        "venue_address": " ".join(filter(None, (
            _first(parser.item_values, "streetaddress"),
            _first(parser.item_values, "postalcode"),
            _first(parser.item_values, "addresslocality"),
        ))) or _visible_labeled_value(document, "Adresse", "Anschrift"),
    }
    for item in _jsonld_candidates(document or "", str(event.get("title") or ""))[:1]:
        structured_price = common._jsonld_admission_price(item)
        if structured_price is not None:
            context["price"] = structured_price
        location = item.get("location")
        if isinstance(location, dict):
            context["venue"] = common.clean_html(str(location.get("name") or "")) or context["venue"]
            address = location.get("address")
            if isinstance(address, dict):
                structured_address = " ".join(
                    common.clean_html(str(address.get(key) or ""))
                    for key in ("streetAddress", "postalCode", "addressLocality")
                    if address.get(key)
                )
                context["venue_address"] = structured_address or context["venue_address"]
    return context


def _richer(candidate: str, current: str) -> bool:
    candidate_text = common.clean_html(candidate)
    current_text = common.clean_html(current)
    return bool(candidate_text and len(candidate_text) >= len(current_text) + max(40, len(current_text) // 5))


def apply_detail_context(event: dict, context: dict[str, str]) -> dict:
    """Merge only facts that improve the source record."""
    enriched = dict(event)
    if _richer(context.get("description", ""), str(event.get("description") or "")):
        # Plain text is duplicated into planner/search payloads; keep a long,
        # sentence-safe searchable excerpt there while description_html retains
        # the complete sanitized event document for the detail page.
        enriched["description"] = common.concise_description(
            context["description"], max_chars=8000,
        )
        enriched["description_html"] = context.get("description_html", "")
        enriched["description_source"] = "scraped"
    elif (
        context.get("description_html")
        and richtext.text_length(context["description_html"]) >= richtext.text_length(str(event.get("description_html") or ""))
        and richtext.describes_same_copy(context["description_html"], str(event.get("description") or ""))
    ):
        enriched["description_html"] = context["description_html"]

    price = context.get("price", "")
    if price:
        enriched["price"] = common.clean_html(price)[:160]
        enriched["admission_basis"] = "explicit"
    for field in ("venue", "venue_address"):
        if not str(enriched.get(field) or "").strip() and context.get(field):
            enriched[field] = context[field]
    return enriched


def enrich_events(events: list[dict], *, cache_namespace: str = _GENERIC_CACHE_NAMESPACE) -> list[dict]:
    """Enrich unique public detail links, failing soft per event.

    A URL shared by several events is normally an overview or rolling article;
    treating it as one event's detail page causes cross-card contamination.
    """
    if not enabled():
        return events
    eligible_ids: set[int] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            if common.event_in_window(event):
                eligible_ids.add(id(event))
        except (AttributeError, TypeError, ValueError):
            # Structurally invalid records are rejected by canonical validation;
            # they must not trigger network requests first.
            continue
    link_counts = Counter(
        str(event.get("link") or "")
        for event in events
        if isinstance(event, dict) and id(event) in eligible_ids
    )
    enriched: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            enriched.append(event)
            continue
        if id(event) not in eligible_ids:
            enriched.append(event)
            continue
        link = str(event.get("link") or "")
        if link_counts[link] != 1 or not _candidate_url(link):
            enriched.append(event)
            continue
        try:
            hostname = (urlsplit(link).hostname or "").casefold()
            document = common.fetch_detail_url(
                link,
                cache_namespace=cache_namespace,
                timeout=20,
                brightdata_fallback=True,
                allowed_hosts=(hostname,),
                cache_failures=True,
            )
            enriched.append(apply_detail_context(event, extract_detail_context(document, event)))
        except Exception as exc:
            common.log_source_error(f"{event.get('source') or 'event'} detail", exc)
            enriched.append(event)
    return enriched
