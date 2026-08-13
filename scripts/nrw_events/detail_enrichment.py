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
from datetime import datetime
from html import escape, unescape
from html.parser import HTMLParser
import json
import os
import re
import time
from urllib.parse import urldefrag, urlsplit

from . import common, richtext


_NON_DOCUMENT_SUFFIXES = (
    ".css", ".csv", ".gif", ".ics", ".jpeg", ".jpg", ".json", ".pdf",
    ".png", ".svg", ".webp", ".xml", ".zip",
)
_SKIPPED_HOSTS = {
    "example.com", "example.org", "example.test", "kihapp.com", "localhost",
    "www.example.com", "www.example.org", "www.kihapp.com",
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


def _needs_detail(event: dict) -> bool:
    """Limit the expensive second pass to genuinely incomplete teasers."""
    description = richtext.to_plain_text(str(
        event.get("description_html") or event.get("description") or ""
    )).strip()
    return len(description) < 240 or _invalid_short_venue(str(event.get("venue") or ""))


def _invalid_short_venue(value: str) -> bool:
    """Treat empty and one-character venue fragments as missing source data."""
    return len(re.sub(r"[^a-z0-9]+", "", common.clean_html(value).casefold())) <= 1


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
    return common.clean_html(match.group(1)).lstrip(" :–-")[:240] if match else ""


def _template_price(document: str) -> str:
    """Extract a price from a known event-only field without broad guessing."""
    if "MyEventButton" in (document or "") and "springmaus-theater.de" in (document or ""):
        for value in re.findall(r'<div[^>]+class=["\']mb-4["\'][^>]*>([^<]+)</div>', document, re.I):
            cleaned = common.clean_html(value)
            if re.search(r"(?:€|\bEUR\b|\bEuro\b)", cleaned, re.I):
                return cleaned[:240]
    return ""


def _adfc_shoebox(document: str, event: dict) -> dict | None:
    """Decode the event payload embedded by the ADFC Ember application."""
    try:
        hostname = (urlsplit(str(event.get("link") or "")).hostname or "").casefold()
    except ValueError:
        return None
    if hostname != "touren-termine.adfc.de":
        return None

    for value in re.findall(
        r'<script[^>]+type=["\']fastboot/shoebox["\'][^>]*>(.*?)</script>',
        document or "",
        re.I | re.S,
    ):
        try:
            payload = json.loads(unescape(value).strip())
            if isinstance(payload, str):
                payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("eventItem"), dict):
            return payload
    return None


def _adfc_table_facts(document: str) -> list[tuple[str, str]]:
    """Read the visitor-facing tour labels paired with their displayed values."""
    match = re.search(
        r'<h[1-6][^>]*>\s*Tourdaten\s*</h[1-6]>(.*?</table>)',
        document or "",
        re.I | re.S,
    )
    if not match:
        return []
    table = match.group(1)
    headings = [common.clean_html(value) for value in re.findall(
        r"<th\b[^>]*>(.*?)</th>", table, re.I | re.S,
    )]
    body = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", table, re.I | re.S)
    values = [common.clean_html(value) for value in re.findall(
        r"<td\b[^>]*>(.*?)</td>", body.group(1) if body else table, re.I | re.S,
    )]
    return [
        (heading, value)
        for heading, value in zip(headings, values)
        if heading and value
    ]


def _display_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _adfc_structured_tour_facts(item: dict) -> list[tuple[str, str]]:
    facts: list[tuple[str, str]] = []
    for label, field, unit in (
        ("Tourlänge", "cTourLengthKm", "km"),
        ("Geschwindigkeit", "cTourSpeedKmh", "km/h"),
        ("Höhenmeter", "cTourHeight", "m"),
    ):
        value = _display_number(item.get(field))
        if value and float(item[field]) > 0:
            facts.append((label, f"{value} {unit}"))
    return facts


def _adfc_price(payload: dict) -> str:
    prices: list[str] = []
    for item in payload.get("eventItemPrices") or []:
        if not isinstance(item, dict):
            continue
        amount = _display_number(item.get("price"))
        if not amount:
            continue
        label = common.clean_html(str(item.get("groupName") or ""))
        value = "kostenfrei" if float(item["price"]) == 0 else f"{amount} €"
        rendered = f"{label}: {value}" if label else value
        if rendered not in prices:
            prices.append(rendered)
    return ", ".join(prices)[:240]


def _adfc_location(payload: dict) -> tuple[str, str]:
    locations = [
        item for item in (payload.get("tourLocations") or [])
        if isinstance(item, dict)
    ]
    if not locations:
        return "", ""
    locations.sort(key=lambda item: (
        str(item.get("type") or "").casefold() != "startpunkt",
        int(item.get("position") or 0),
    ))
    location = locations[0]
    street = common.clean_html(str(location.get("street") or ""))
    city_line = " ".join(filter(None, (
        common.clean_html(str(location.get("zipCode") or "")),
        common.clean_html(str(location.get("city") or "")),
    )))
    address = ", ".join(filter(None, (street, city_line)))
    venue = common.clean_html(str(location.get("name") or "")) or street
    return venue[:300], address[:500]


def _adfc_detail_context(document: str, event: dict) -> dict[str, str] | None:
    payload = _adfc_shoebox(document, event)
    if payload is None:
        return None
    item = payload["eventItem"]
    short = common.clean_html(str(item.get("cShortDescription") or ""))
    full_html = richtext.sanitize_rich_text(str(item.get("description") or ""))
    full_text = richtext.to_plain_text(full_html)
    blocks: list[str] = []
    if short and short.casefold() not in full_text.casefold():
        blocks.append(f"<p>{escape(short, quote=False)}</p>")
    if full_html:
        blocks.append(full_html)

    tour_facts = _adfc_table_facts(document) or _adfc_structured_tour_facts(item)
    if tour_facts:
        blocks.extend((
            "<h3>Tourdaten</h3>",
            "<ul>" + "".join(
                f"<li><strong>{escape(label, quote=False)}:</strong> "
                f"{escape(value, quote=False)}</li>"
                for label, value in tour_facts
            ) + "</ul>",
        ))

    tags: dict[str, list[str]] = {}
    for tag in payload.get("itemTags") or []:
        if not isinstance(tag, dict):
            continue
        category = common.clean_html(str(tag.get("category") or ""))
        value = common.clean_html(str(tag.get("tag") or ""))
        if category and value and value not in tags.setdefault(category, []):
            tags[category].append(value)
    if tags:
        blocks.extend((
            "<h3>Merkmale</h3>",
            "<ul>" + "".join(
                f"<li><strong>{escape(category, quote=False)}:</strong> "
                f"{escape(', '.join(values), quote=False)}</li>"
                for category, values in tags.items()
            ) + "</ul>",
        ))

    description_html = richtext.sanitize_rich_text("".join(blocks))
    venue, venue_address = _adfc_location(payload)
    return {
        "description": richtext.to_plain_text(description_html),
        "description_html": description_html,
        "price": _adfc_price(payload),
        "venue": venue,
        "venue_address": venue_address,
    }


def _event_hostname(event: dict) -> str:
    try:
        return (urlsplit(str(event.get("link") or "")).hostname or "").casefold()
    except ValueError:
        return ""


def _title_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", common.clean_html(value).casefold())


def _context_from_fragment(
    fragment: str, *, price: str = "", venue: str = "", venue_address: str = "",
) -> dict[str, str]:
    description_html = richtext.sanitize_rich_text(fragment)
    return {
        "description": richtext.to_plain_text(description_html),
        "description_html": description_html,
        "price": common.clean_html(price)[:240],
        "venue": common.clean_html(venue)[:300],
        "venue_address": common.clean_html(venue_address)[:500],
    }


def _klimaviertel_overview_context(document: str, event: dict) -> dict[str, str] | None:
    """Match one event on Klimaviertel's shared calendar by title and date."""
    if _event_hostname(event) not in {"klimaviertel-beuel.de", "www.klimaviertel-beuel.de"}:
        return None
    wanted_title = _title_key(str(event.get("title") or ""))
    wanted_date = str(event.get("start_date") or event.get("date") or "")[:10]
    item = next((
        candidate
        for candidate in common.jsonld_event_items(document or "")
        if isinstance(candidate, dict)
        and _title_key(str(candidate.get("name") or "")) == wanted_title
        and str(candidate.get("startDate") or "")[:10] == wanted_date
    ), None)
    if item is None:
        # This URL contains several events. Never fall back to a neighbouring
        # JSON-LD record when the requested occurrence is not on the page.
        return _context_from_fragment("")

    description_html = richtext.sanitize_rich_text(str(item.get("description") or ""))
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    address_parts = [
        common.clean_html(str(address.get(key) or ""))
        for key in ("streetAddress", "postalCode", "addressLocality")
    ]
    venue_address = " ".join(dict.fromkeys(part for part in address_parts if part))
    price = common._jsonld_admission_price(item)
    return {
        "description": richtext.to_plain_text(description_html),
        "description_html": description_html,
        "price": price or "",
        "venue": common.clean_html(str(location.get("name") or ""))[:300],
        "venue_address": venue_address[:500],
    }


def _pantheon_detail_context(document: str, event: dict) -> dict[str, str] | None:
    if _event_hostname(event) not in {"pantheon.de", "www.pantheon.de"}:
        return None
    fragment = urlsplit(str(event.get("link") or "")).fragment
    event_id = fragment.removeprefix("t") if re.fullmatch(r"t\d+", fragment) else ""
    blocks = re.findall(r'<li\b[^>]*id=["\']t(\d+)["\'][^>]*>(.*?)</li>', document or "", re.I | re.S)
    title_key = _title_key(str(event.get("title") or ""))
    block = ""
    for candidate_id, candidate in blocks:
        title_match = re.search(r'class=["\'][^"\']*\bevent-title\b[^"\']*["\'][^>]*>(.*?)</h2>', candidate, re.I | re.S)
        candidate_title = _title_key(title_match.group(1) if title_match else "")
        if (event_id and candidate_id == event_id) or (title_key and candidate_title == title_key):
            block = candidate
            break
    if not block:
        return None
    detail = re.search(
        r'<div\b[^>]+class=["\'][^"\']*\bevent-detail\b[^"\']*["\'][^>]*>(.*?)(?=<div\b[^>]+class=["\'][^"\']*\bevent-less\b|</div>\s*</div>\s*<div\b[^>]+class=["\'][^"\']*\bevent-foot\b)',
        block, re.I | re.S,
    )
    body = detail.group(1) if detail else ""
    body = re.sub(r'<div\b[^>]+class=["\'][^"\']*\bbImage\b[^"\']*["\'][^>]*>.*?</div>', "", body, flags=re.I | re.S)
    body = re.sub(r'<div\b[^>]+class=["\'][^"\']*\bbLink\b[^"\']*["\'][^>]*>.*?</div>', "", body, flags=re.I | re.S)
    ticket = re.search(r'<dl\b[^>]+class=["\'][^"\']*\bevent-ticket-detail\b[^"\']*["\'][^>]*>(.*?)</dl>', block, re.I | re.S)
    ticket_text = common.clean_html(ticket.group(1) if ticket else "")
    amount = re.search(r"\bEUR\s*(\d+(?:[.,]\d{1,2})?)", ticket_text, re.I)
    price = f"{amount.group(1).replace('.', ',')} € im Vorverkauf" if amount else ""
    return _context_from_fragment(body, price=price)


def _rheinbach_sommerkino_context(document: str, event: dict) -> dict[str, str] | None:
    if _event_hostname(event) not in {"wir-fuer-rheinbach.de", "www.wir-fuer-rheinbach.de"}:
        return None
    if "sommerkino" not in str(event.get("link") or "").casefold():
        return None
    intro = re.search(
        r'<h2\b[^>]*>\s*Sommerkino\s+für\s+den\s+guten\s+Zweck\s*</h2>(.*?)(?=<h2\b|<div\b[^>]+id=["\']c3190)',
        document or "", re.I | re.S,
    )
    info = re.search(
        r'<h2\b[^>]*>\s*(?:<strong>)?Informationen\s+zum\s+Rheinbacher\s+Sommerkino(?:</strong>)?\s*</h2>(.*?)(?=</div>\s*</div>|<div\b[^>]+id=["\']c3625|$)',
        document or "", re.I | re.S,
    )
    fragment = "".join(filter(None, (
        intro.group(1) if intro else "",
        "<h3>Besuchsinformationen</h3>" + info.group(1) if info else "",
    )))
    if not richtext.to_plain_text(richtext.sanitize_rich_text(fragment)):
        return None
    info_text = common.clean_html(info.group(1) if info else "")
    price_match = re.search(r"Karten\s+kosten\s+(?:im\s+Vorverkauf\s+)?(?:weiterhin\s+)?(\d+(?:[,.]\d+)?)\s*Euro", info_text, re.I)
    price = f"{price_match.group(1)} Euro im Vorverkauf" if price_match else ""
    return _context_from_fragment(
        fragment, price=price, venue_address="Bachstraße, Rheinbach",
    )


def _unkel_detail_context(document: str, event: dict) -> dict[str, str] | None:
    if _event_hostname(event) not in {"rhein.info", "www.rhein.info"}:
        return None
    if "/unkel" not in urlsplit(str(event.get("link") or "")).path.casefold():
        return None
    wanted_title = _title_key(str(event.get("title") or ""))
    wanted_date = str(event.get("start_date") or event.get("date") or "")
    rows: list[tuple[bool, str]] = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", document or "", re.I | re.S):
        heading = re.search(r'class=["\'][^"\']*\baccordion_head\b[^"\']*["\'][^>]*>(.*?)</h3>', row, re.I | re.S)
        if not heading:
            continue
        row_title = re.sub(r"\s*\+\s*$", "", common.clean_html(heading.group(1)))
        if _title_key(row_title) != wanted_title:
            continue
        date_text = common.clean_html((re.search(r'class=["\'][^"\']*\bdatum\b[^"\']*["\'][^>]*>(.*?)</div>', row, re.I | re.S) or ["", ""])[1])
        parsed_date = common.parse_date(date_text)
        date_matches = not wanted_date or bool(
            parsed_date and parsed_date.strftime("%Y-%m-%d") == wanted_date
        )
        rows.append((date_matches, row))
    if not rows:
        return None
    row = max(rows, key=lambda item: item[0])[1]
    body = re.search(
        r'class=["\'][^"\']*\baccordion_body\b[^"\']*["\'][^>]*>(.*?)(?=<br\s*/?>\s*<div\b[^>]+class=["\']orgalink|<div\b[^>]+class=["\']orgalink|</td>)',
        row, re.I | re.S,
    )
    fragment = body.group(1) if body else ""
    location = re.search(r'class=["\']locationlink["\'][^>]*>.*?<a\b[^>]*>(.*?)</a>', row, re.I | re.S)
    price = _visible_labeled_value(fragment, "Preis", "Preise", "Kosten", "Eintritt")
    return _context_from_fragment(
        fragment, price=price,
        venue=common.clean_html(location.group(1) if location else ""),
    )


def _rathausmusik_detail_context(document: str, event: dict) -> dict[str, str] | None:
    if _event_hostname(event) not in {"rathausmusik.com", "www.rathausmusik.com"}:
        return None
    title = common.clean_html(str(event.get("title") or ""))
    band = re.sub(r"\s*\([^)]*\)\s*$", "", title.split(":", 1)[1] if ":" in title else "").strip()
    if not band:
        return None
    blocks: list[tuple[int, str]] = []
    for match in re.finditer(
        r'<div\b[^>]*class=["\'][^"\']*\bxr_txt\b[^"\']*["\'][^>]*style=["\'][^"\']*\btop:\s*(-?\d+)px[^"\']*["\'][^>]*>(.*?)</div>',
        document or "", re.I | re.S,
    ):
        text = common.clean_html(match.group(2))
        if text:
            blocks.append((int(match.group(1)), text))
    band_key = _title_key(band)
    headings = [(top, text) for top, text in blocks if band_key and band_key in _title_key(text)]
    if not headings:
        return None
    heading_top, _ = min(headings, key=lambda item: (len(item[1]), item[0]))
    descriptions = [
        (top, text) for top, text in blocks
        if heading_top < top <= heading_top + 500 and len(text) >= 55
    ]
    if not descriptions:
        return None
    _, description = min(descriptions, key=lambda item: item[0])
    return _context_from_fragment(richtext.from_plain_text(description))


def _eitorf_detail_context(document: str, event: dict) -> dict[str, str] | None:
    if _event_hostname(event) not in {"eitorf.de", "www.eitorf.de"}:
        return None
    if "/veranstaltungen/" not in urlsplit(str(event.get("link") or "")).path.casefold():
        return None
    section = re.search(r'<section\b[^>]+class=["\'][^"\']*\bsingle-page\b[^"\']*["\'][^>]*>(.*?)</section>', document or "", re.I | re.S)
    if not section:
        return None
    body = section.group(1)
    content = re.search(r'<div\b[^>]+class=["\']content["\'][^>]*>(.*)', body, re.I | re.S)
    fragment = content.group(1) if content else body
    price_match = re.search(r'class=["\'][^"\']*\bevent-price\b[^"\']*["\'][^>]*>(.*?)</p>', fragment, re.I | re.S)
    price = re.sub(r"^Preis\s*:\s*", "", common.clean_html(price_match.group(1) if price_match else ""), flags=re.I)
    venue_match = re.search(r'class=["\'][^"\']*\bevent-place\b[^"\']*["\'][^>]*>(.*?)</p>', fragment, re.I | re.S)
    return _context_from_fragment(
        fragment, price=price,
        venue=common.clean_html(venue_match.group(1) if venue_match else ""),
    )


def _heading_section(document: str, heading: str) -> str:
    match = re.search(
        rf'<h[1-6]\b[^>]*>\s*{heading}\s*</h[1-6]>(.*?)(?=<h[1-6]\b|</article>)',
        document or "", re.I | re.S,
    )
    return match.group(1) if match else ""


def _froscon_detail_context(document: str, event: dict) -> dict[str, str] | None:
    if _event_hostname(event) not in {"froscon.org", "www.froscon.org"}:
        return None
    article = re.search(r'<article\b[^>]+id=["\']content["\'][^>]*>(.*?)</article>', document or "", re.I | re.S)
    if not article:
        return None
    body = article.group(1)
    schedule = _heading_section(body, r"Ort\s*&(?:amp;)?\s*Uhrzeit")
    tickets = _heading_section(body, "Tickets")
    catering = _heading_section(body, "Verpflegung")
    fragment = "<h3>Ort &amp; Uhrzeit</h3>" + schedule + "<h3>Tickets</h3>" + tickets
    if catering:
        fragment += "<h3>Verpflegung</h3>" + catering
    first_paragraph = re.search(r"<p\b[^>]*>(.*?)</p>", schedule, re.I | re.S)
    address_parts = [
        common.clean_html(part) for part in re.split(r"<br\s*/?>", first_paragraph.group(1) if first_paragraph else "", flags=re.I)
        if common.clean_html(part)
    ]
    venue = address_parts[0] if address_parts else ""
    address = ", ".join(address_parts[1:])
    price = "kostenlos" if re.search(r"Eintritt\s+zur\s+FrOSCon\s+ist\s+frei", common.clean_html(tickets), re.I) else ""
    return _context_from_fragment(fragment, price=price, venue=venue, venue_address=address)


def _bundeskunsthalle_detail_context(document: str, event: dict) -> dict[str, str] | None:
    """Read the editorial intro grid from an exhibition detail page.

    Bundeskunsthalle pages have no Event JSON-LD or semantic description
    attribute. Their complete introduction is split across several ``ce-wrap``
    blocks inside the first content section; later sections are galleries,
    accordions and related programme and must not be absorbed.
    """
    if _event_hostname(event) not in {"bundeskunsthalle.de", "www.bundeskunsthalle.de"}:
        return None
    main = re.search(r'<main\b[^>]+id=["\']main-content["\'][^>]*>(.*)', document or "", re.I | re.S)
    if not main:
        return None
    intro = re.search(
        r'<section\b[^>]+class=["\'][^"\']*\bpt-0\b[^"\']*["\'][^>]*>(.*?)</section>',
        main.group(1), re.I | re.S,
    )
    if not intro:
        return None
    blocks = re.findall(
        r'<div\b[^>]+class=["\'][^"\']*\bce-wrap\b[^"\']*["\'][^>]*>(.*?)</div>',
        intro.group(1), re.I | re.S,
    )
    fragment = "".join(blocks)
    price = "kostenlos" if re.search(
        r'class=["\'][^"\']*page-header__date[^"\']*["\'][^>]*>[^<]*(?:Admission\s+free|Eintritt\s+frei)',
        main.group(1), re.I | re.S,
    ) else ""
    context = _context_from_fragment(fragment, price=price)
    return context if context["description"] else None


def _dein_phonzimmer_detail_context(document: str, event: dict) -> dict[str, str] | None:
    """Read the bounded WordPress article, including one matching series date."""
    if _event_hostname(event) not in {"dein-phonzimmer.de", "www.dein-phonzimmer.de"}:
        return None
    entry = re.search(
        r'<div\b[^>]+class=["\'][^"\']*\bentry-content\b[^"\']*["\'][^>]*>'
        r'(.*?)(?=</article>)',
        document or "", re.I | re.S,
    )
    if not entry:
        return None

    body = entry.group(1)
    # The shared Mirecourtplatz page contains a common introduction followed by
    # several dated programme rows and galleries. Keep the common visitor facts
    # plus only the row belonging to this occurrence.
    schedule = re.search(
        r'<p\b[^>]*>\s*<strong>\s*Termine:\s*</strong>\s*</p>', body, re.I | re.S,
    )
    if schedule:
        intro = body[:schedule.start()]
        wanted_date = str(event.get("start_date") or event.get("date") or "")[:10]
        date_label = ""
        try:
            date_label = datetime.strptime(wanted_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            pass
        occurrence = ""
        if date_label:
            match = re.search(
                rf'<p\b[^>]*>(?:(?!</p>).)*?\b{re.escape(date_label)}\b(?:(?!</p>).)*?</p>',
                body[schedule.end():], re.I | re.S,
            )
            occurrence = match.group(0) if match else ""
        body = intro + occurrence

    body = re.sub(
        r'<p\b[^>]*>\s*<a\b[^>]*>\s*zurück\s+zur\s+Startseite\s*</a>\s*</p>',
        "", body, flags=re.I | re.S,
    )
    body = re.sub(r'<figure\b.*?</figure>', "", body, flags=re.I | re.S)
    context = _context_from_fragment(body)
    return context if context["description"] else None


def _source_specific_detail_context(document: str, event: dict) -> dict[str, str] | None:
    for extractor in (
        _klimaviertel_overview_context,
        _pantheon_detail_context,
        _rheinbach_sommerkino_context,
        _unkel_detail_context,
        _rathausmusik_detail_context,
        _eitorf_detail_context,
        _froscon_detail_context,
        _bundeskunsthalle_detail_context,
        _dein_phonzimmer_detail_context,
    ):
        context = extractor(document, event)
        if context is not None:
            return context
    return None


def _master_data_only(event: dict) -> bool:
    source_id = str(event.get("source_id") or "").casefold()
    source = str(event.get("source") or "").casefold()
    return (
        source_id == "ruhr-guide" or source_id == "meetup"
        or source_id.startswith("meetup-") or source in {"meetup", "ruhr-guide"}
    )


def _supports_repeated_detail(event: dict) -> bool:
    host = _event_hostname(event)
    return host in {
        "klimaviertel-beuel.de", "www.klimaviertel-beuel.de",
        "pantheon.de", "www.pantheon.de",
        "rhein.info", "www.rhein.info",
        "rathausmusik.com", "www.rathausmusik.com",
        "theater-marabu.de", "www.theater-marabu.de",
        "wir-fuer-rheinbach.de", "www.wir-fuer-rheinbach.de",
        "dein-phonzimmer.de", "www.dein-phonzimmer.de",
    }


def extract_detail_context(document: str, event: dict) -> dict[str, str]:
    """Extract richer, auditable fields from one event detail document."""
    adfc_context = _adfc_detail_context(document, event)
    if adfc_context is not None:
        return adfc_context
    source_context = _source_specific_detail_context(document, event)
    if source_context is not None:
        return source_context
    # These URLs are shared program/overview documents.  If their bounded
    # extractor cannot identify the requested title, generic whole-document
    # extraction would attach a neighbouring event's copy and admission.
    host = _event_hostname(event)
    path = urlsplit(str(event.get("link") or "")).path.casefold().rstrip("/")
    if (
        host in {"pantheon.de", "www.pantheon.de"}
        or (host in {"rhein.info", "www.rhein.info"} and path == "/unkel")
    ):
        return {
            "description": "", "description_html": "", "price": "",
            "venue": "", "venue_address": "",
        }
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
                address_parts: list[str] = []
                for key in ("streetAddress", "postalCode", "addressLocality"):
                    part = common.clean_html(str(address.get(key) or ""))
                    current = " ".join(address_parts).casefold()
                    if part and part.casefold() not in current:
                        address_parts.append(part)
                structured_address = " ".join(address_parts)
                context["venue_address"] = structured_address or context["venue_address"]
    if _master_data_only(event):
        context["description"] = ""
        context["description_html"] = ""
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
        # Listing teasers may have been classified before this stronger detail
        # evidence existed. Reopen only inferred decisions; explicit adapter
        # locks remain authoritative at the canonical boundary.
        if not str(event.get("category_reason") or "").startswith("source:locked-default:"):
            for field in (
                "category_key", "category_label", "category_confidence", "category_reason",
            ):
                enriched.pop(field, None)
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
    return enriched


def enrich_events(events: list[dict], *, cache_namespace: str = _GENERIC_CACHE_NAMESPACE) -> list[dict]:
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
    documents: dict[str, str] = {}
    enriched: list[dict] = []
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
            enriched.append(event)
            continue
        link = str(event.get("link") or "")
        fetch_link = urldefrag(link)[0]
        if (
            (link_counts[fetch_link] != 1 and not _supports_repeated_detail(event))
            or not _candidate_url(fetch_link)
        ):
            enriched.append(event)
            continue
        try:
            if fetch_link not in documents:
                hostname = (urlsplit(fetch_link).hostname or "").casefold()
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
            common.log_source_error(f"{event.get('source') or 'event'} detail", exc)
            enriched.append(event)
    return enriched
