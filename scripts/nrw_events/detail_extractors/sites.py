"""Bounded proprietary detail parsers. No network or persistent state."""
from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime
from html import escape, unescape
from urllib.parse import urlsplit

from .. import common, richtext
from ..detail_parsing import _exact_jsonld_description, _tribe_price, _visible_labeled_value
from ..detail_types import DetailContext
from ..models import RawEvent


def _template_price(document: str) -> str:
    """Extract a price from a known event-only field without broad guessing."""
    if price := _tribe_price(document):
        return price
    if "MyEventButton" in (document or "") and "springmaus-theater.de" in (document or ""):
        for value in re.findall(r'<div[^>]+class=["\']mb-4["\'][^>]*>([^<]+)</div>', document, re.I):
            cleaned = common.clean_html(value)
            if re.search(r"(?:€|\bEUR\b|\bEuro\b)", cleaned, re.I):
                return cleaned[:240]
    return ""


def _arp_museum_subline_price(document: str, event: RawEvent) -> str:
    """Prefer Arp Museum's exact event-headline admission over plugin metadata."""
    if str(event.get("source_id") or "").casefold() != "arp-museum":
        return ""
    try:
        hostname = (urlsplit(str(event.get("link") or "")).hostname or "").casefold()
    except ValueError:
        return ""
    if hostname not in {"arpmuseum.org", "www.arpmuseum.org"}:
        return ""
    labeled_price = _visible_labeled_value(
        document, "Preis", "Preise", "Kosten", "Eintritt",
    )
    if re.search(
        r"(?:\bzzgl\.?|\bzuzüglich|\bzuzueglich)\s+"
        r"[^.!?;]{0,40}\bmuseumseintritt\b",
        labeled_price,
        re.I,
    ):
        return ""
    headline = re.search(
        r'<section[^>]+class=["\'][^"\']*\bce-page-headline\b[^"\']*["\'][^>]*>'
        r'(.*?)</section>',
        document or "",
        re.I | re.S,
    )
    if not headline:
        return ""
    subline = re.search(
        r'<p[^>]+class=["\'][^"\']*\bsubline\b[^"\']*["\'][^>]*>(.*?)</p>',
        headline.group(1),
        re.I | re.S,
    )
    value = common.clean_html(subline.group(1)).casefold() if subline else ""
    normalized = re.sub(r"[^a-zäöüß]+", " ", value).strip()
    explicit_free = re.search(
        r"\bsonder veranstaltung (?:kostenfrei{1,2}|eintritt frei)"
        r"(?: keine anmeldung erforderlich)?$",
        normalized,
    )
    return "kostenlos" if explicit_free else ""


def _adfc_shoebox(document: str, event: RawEvent) -> dict | None:
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
        for heading, value in zip(headings, values, strict=False)
        if heading and value
    ]


def _display_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
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


def _adfc_detail_context(document: str, event: RawEvent) -> DetailContext | None:
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


def _event_hostname(event: RawEvent) -> str:
    try:
        return (urlsplit(str(event.get("link") or "")).hostname or "").casefold()
    except ValueError:
        return ""


def _title_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", common.clean_html(value).casefold())


def _context_from_fragment(
    fragment: str, *, price: str = "", venue: str = "", venue_address: str = "",
) -> DetailContext:
    description_html = richtext.sanitize_rich_text(fragment)
    return {
        "description": richtext.to_plain_text(description_html),
        "description_html": description_html,
        "price": common.clean_html(price)[:240],
        "venue": common.clean_html(venue)[:300],
        "venue_address": common.clean_html(venue_address)[:500],
    }


def _klimaviertel_overview_context(document: str, event: RawEvent) -> DetailContext | None:
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
    raw_location = item.get("location")
    location = raw_location if isinstance(raw_location, dict) else {}
    raw_address = location.get("address")
    address = raw_address if isinstance(raw_address, dict) else {}
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


def _pantheon_detail_context(document: str, event: RawEvent) -> DetailContext | None:
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


def _rheinbach_sommerkino_context(document: str, event: RawEvent) -> DetailContext | None:
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


def _unkel_detail_context(document: str, event: RawEvent) -> DetailContext | None:
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


def _rathausmusik_detail_context(document: str, event: RawEvent) -> DetailContext | None:
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


def _eitorf_detail_context(document: str, event: RawEvent) -> DetailContext | None:
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


def _froscon_detail_context(document: str, event: RawEvent) -> DetailContext | None:
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


def _bundeskunsthalle_detail_context(document: str, event: RawEvent) -> DetailContext | None:
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


def _dein_phonzimmer_detail_context(document: str, event: RawEvent) -> DetailContext | None:
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
        with contextlib.suppress(ValueError):
            date_label = datetime.strptime(wanted_date, "%Y-%m-%d").strftime("%d.%m.%Y")
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


def _bildungswerk_brotfabrik_context(document: str, event: RawEvent) -> DetailContext | None:
    """The query URL can return a shared registration page without the event."""
    if _event_hostname(event) not in {"bildungswerk-brotfabrik.de", "www.bildungswerk-brotfabrik.de"}:
        return None
    if urlsplit(str(event.get("link") or "")).path.casefold().rstrip("/") != "/workshops":
        return None
    # Keep the authoritative calendar API copy unless title and occurrence
    # identity prove that the document contains this event's own description.
    # Generic extraction would promote registration terms and cancellation
    # conditions into event facts, even when the URL has a unique IDT query.
    _description, description_html = _exact_jsonld_description(document, event)
    return _context_from_fragment(description_html)

