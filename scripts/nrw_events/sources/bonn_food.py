"""Primary food and drink event calendars in and around Bonn."""

import json
import re
from datetime import datetime, timedelta

from .. import common
from . import regional_common as rc


_CRAFTQUELLE_URL = "https://craft-quelle.de/neue-tasting-termine/"
_BFF_URL = "https://bff-bonn.com/kulinarische-highlights-bonn"
_VOMFASS_URL = "https://www.vomfass.de/pages/tastings"
_BIERTASTING_URL = "https://www.biertasting-bonn.de/"
_LUDWIGS_URL = "https://www.ludwigs-bonn.de/veranstaltungen"
_REDUETTCHEN_URL = "https://reduettchen.de/events/"
_STREET_FOOD_URL = "https://www.street-food-bonn.de/"
_VOMFASS_ALLOWED_HOSTS = ("www.vomfass.de",)

_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}


def fetch_craftquelle() -> list:
    return rc.fetch_html_events(
        "Craftquelle Bonn",
        _CRAFTQUELLE_URL,
        lambda html: events_from_craftquelle(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="craftquelle-bonn", timeout=20),
        ),
    )


def fetch_bff() -> list:
    return rc.fetch_html_events("BFF Bonner Schifffahrt", _BFF_URL, events_from_bff)


def fetch_vomfass() -> list:
    return rc.fetch_html_events(
        "vomFASS Bonn",
        _VOMFASS_URL,
        lambda html: events_from_vomfass(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="vomfass-bonn", timeout=20,
                brightdata_fallback=True,
                allowed_hosts=_VOMFASS_ALLOWED_HOSTS,
                required_body_markers=("application/ld+json",)),
        ),
        fetcher=lambda url, timeout: common.fetch_url_with_brightdata_fallback(
            url, timeout=timeout, allowed_hosts=_VOMFASS_ALLOWED_HOSTS,
            required_body_markers=("data-event-card",)),
    )


def fetch_biertasting() -> list:
    return rc.fetch_html_events("Biertasting Bonn", _BIERTASTING_URL, events_from_biertasting)


def fetch_ludwigs() -> list:
    return rc.fetch_html_events(
        "Ludwig's Bonn",
        _LUDWIGS_URL,
        lambda html: events_from_ludwigs(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="ludwigs-bonn", timeout=20),
        ),
    )


def fetch_reduettchen() -> list:
    return rc.fetch_html_events(
        "Redüttchen",
        _REDUETTCHEN_URL,
        lambda html: events_from_reduettchen(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="reduettchen", timeout=20),
        ),
    )


def fetch_street_food() -> list:
    return rc.fetch_html_events("Street Food Bonn", _STREET_FOOD_URL, events_from_street_food)


def events_from_craftquelle(html: str, detail_fetcher=None) -> list:
    events = []
    for row in re.findall(r"<tr\b.*?</tr>", html or "", re.S | re.I):
        cells = [rc.clean(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        if len(cells) < 4 or cells[0].casefold() == "datum":
            continue
        href = _match(r"href=['\"]([^'\"]+)['\"]", row)
        start = _parse_german_date(cells[0]) or _date_from_href(href)
        if not start:
            continue
        link = rc.abs_url(_CRAFTQUELLE_URL, href) if href else _CRAFTQUELLE_URL
        title = cells[1]
        listing_description = f"{title}. Leitung: {cells[2]}." if cells[2] else title
        description = listing_description
        venue = "Brauwerkstatt Bonn, Hermannstraße 104, 53225 Bonn"
        end = start
        if href and detail_fetcher and _in_window(start):
            detail = _safe_detail(detail_fetcher, link, "Craftquelle Bonn")
            if detail:
                parsed = _craftquelle_detail(detail)
                start = parsed.get("start") or start
                end = parsed.get("end") or start
                venue = parsed.get("venue") or venue
                description = _food_description(parsed.get("description")) or listing_description
        if re.search(r"ausverkauft|0\s+plätze", cells[0], re.I):
            description = _append_sentence(description, "Ausverkauft.")
        ev = common.make_event(
            title, start, end, venue, "Bonn", description, link,
            "Craftquelle Bonn", "bier tasting braukurs genuss", 0.98,
        )
        if ev:
            ev["price"] = _price(cells[3])
            events.append(_force_food(ev))
    return rc.dedupe(events)


def events_from_bff(html: str) -> list:
    return _events_from_schema_html(
        html, source="BFF Bonner Schifffahrt", default_url=_BFF_URL,
        default_city="Bonn", category="kulinarische schifffahrt dinner brunch genuss",
    )


def events_from_vomfass(html: str, detail_fetcher=None) -> list:
    events = []
    for article in re.findall(r"<article\b[^>]*data-event-card[^>]*>.*?</article>", html or "", re.S | re.I):
        attrs = article.split(">", 1)[0]
        city = _match(r"data-city=['\"]([^'\"]+)", attrs).casefold()
        partner = _match(r"data-partner=['\"]([^'\"]+)", attrs).casefold()
        if city != "bonn" and partner != "vomfass-bonn":
            continue
        date_raw = _match(r"data-date=['\"]([^'\"]+)", attrs)
        href = _match(r"<h3[^>]*>\s*<a[^>]+href=['\"]([^'\"]+)", article)
        title = rc.clean(_match(r"<h3[^>]*class=['\"][^'\"]*ef-card__title[^'\"]*['\"][^>]*>(.*?)</h3>", article))
        if not (date_raw and href and title):
            continue
        link = rc.abs_url(_VOMFASS_URL, href)
        start = rc.with_time(common.parse_iso_date(date_raw), rc.clean(_match(r"ef-card__time[^>]*>(.*?)</", article)))
        price = _price(rc.clean(_match(r"ef-card__price[^>]*>(.*?)</div>", article)))
        venue = "vomFASS Bonn, Friedrichstraße 49, 53111 Bonn"
        description = f"Tasting bei vomFASS Bonn: {title}."
        end = start
        if detail_fetcher and _in_window(start):
            detail = _safe_detail(detail_fetcher, link, "vomFASS Bonn")
            if detail:
                detailed = _events_from_schema_html(
                    detail, source="vomFASS Bonn", default_url=link,
                    default_city="Bonn", category="tasting spirituosen wein genuss",
                )
                if detailed:
                    detailed_event = detailed[0]
                    if not detailed_event.get("price"):
                        detailed_event["price"] = price
                    events.append(detailed_event)
                    continue
        ev = common.make_event(
            title, start, end, venue, "Bonn", description, link,
            "vomFASS Bonn", "tasting spirituosen wein genuss", 0.98,
        )
        if ev:
            ev["price"] = price
            events.append(_force_food(ev))
    return rc.dedupe(events)


def events_from_biertasting(html: str) -> list:
    text = rc.clean(html)
    section = _between(text, "Terminliste Tastings", "Informationen & Links")
    year_match = re.search(r"bis\s+Dezember\s+(20\d{2})", section, re.I)
    if not year_match:
        return []
    year = int(year_match.group(1))
    venue = _match(r"Ort:\s*(.*?)\s+Preis:", section) or "Atelier Zwei Zwei Drei, Mainzer Str. 223, Bonn-Mehlem"
    event_pattern = re.compile(
        r"(Donnerstag|Freitag|Samstag|Sonntag),\s*(\d{1,2})\.\s*"
        r"(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s+"
        r"(.*?)(?:\s*\((\d+(?:[,.]\d+)?\s*€)\))"
        r"(?=\s+(?:Donnerstag|Freitag|Samstag|Sonntag),|"
        r"\s+(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\b|$)",
        re.I,
    )
    times = {"donnerstag": (19, 0), "freitag": (20, 0), "samstag": (20, 0), "sonntag": (18, 0)}
    events = []
    for weekday, day, month_name, title, price in event_pattern.findall(section):
        month = _MONTHS[month_name.casefold()]
        hour, minute = times[weekday.casefold()]
        start = datetime(year, month, int(day), hour, minute)
        end = start + timedelta(hours=3)
        ev = common.make_event(
            rc.clean(title), start, end, venue, "Bonn",
            f"Geführtes Biertasting: {rc.clean(title)}.", _BIERTASTING_URL,
            "Biertasting Bonn", "bier tasting verkostung genuss", 0.95,
        )
        if ev:
            ev["price"] = _price(price)
            events.append(_force_food(ev))
    return rc.dedupe(events)


def events_from_ludwigs(html: str, detail_fetcher=None) -> list:
    events = []
    for card in re.findall(r"<div class=['\"]card\s+no-r['\"]>.*?</div>\s*</div>", html or "", re.S | re.I):
        href = _match(r"href=['\"]([^'\"]*\/veranstaltungen/termin/20\d{2}/\d{2}/[^'\"]+)", card)
        title = rc.clean(_match(r"<h3[^>]*>(.*?)</h3>", card))
        if not (href and title):
            continue
        link = rc.abs_url(_LUDWIGS_URL, href)
        date_match = re.search(r"/termin/(20\d{2})/(\d{2})/", href)
        day_match = re.search(r"<p[^>]*class=['\"]small mb-1['\"][^>]*>\s*(\d{1,2})\.", card, re.I)
        if not (date_match and day_match):
            continue
        start = datetime(int(date_match.group(1)), int(date_match.group(2)), int(day_match.group(1)))
        description = rc.clean(_match(r"<h3[^>]*>.*?</h3>\s*<p>(.*?)</p>", card))
        end = start
        if detail_fetcher and _in_window(start):
            detail = _safe_detail(detail_fetcher, link, "Ludwig's Bonn")
            if detail:
                parsed = _ludwigs_detail(detail, start)
                start = parsed.get("start") or start
                end = parsed.get("end") or start
                description = parsed.get("description") or description
        ev = common.make_event(
            title, start, end, "Ludwig's Restaurant, Am Bonner Bogen 1, 53227 Bonn",
            "Bonn", description, link, "Ludwig's Bonn",
            "restaurant dinner menü bbq wein genuss", 0.98,
        )
        if ev:
            events.append(_force_food(ev))
    return rc.dedupe(events)


def events_from_reduettchen(html: str, detail_fetcher=None) -> list:
    events = []
    blocks = re.findall(
        r"<div[^>]+av_one_third[^>]*>.*?(?=<div[^>]+av_one_third|<div[^>]+av_two_third|\Z)",
        html or "", re.S | re.I,
    )
    for block in blocks:
        title = rc.clean(_match(r"<h2[^>]*>(.*?)</h2>", block))
        text = rc.clean(block)
        if not title:
            continue
        dates = _exact_reduettchen_dates(text)
        if not dates:
            continue
        href = next((item for item in re.findall(r"href=['\"]([^'\"]+)", block, re.I)
                     if item.startswith("http") and "reduettchen.de" in item), "")
        link = href or _REDUETTCHEN_URL
        description = text
        detail_data = {}
        if href and detail_fetcher and any(_in_window(date_value) for date_value in dates):
            detail = _safe_detail(detail_fetcher, link, "Redüttchen")
            if detail:
                detail_data = _reduettchen_detail(detail)
                description = detail_data.get("description") or description
        for date_value in dates:
            start = date_value
            if detail_data.get("time"):
                hour, minute = detail_data["time"]
                start = start.replace(hour=hour, minute=minute)
            ev = common.make_event(
                title, start, start, "Redüttchen, Kurfürstenallee 1, 53177 Bonn-Bad Godesberg",
                "Bonn-Bad Godesberg", description, link, "Redüttchen",
                "restaurant gourmet dinner wein bbq genuss", 0.98,
            )
            if ev:
                ev["price"] = detail_data.get("price", "")
                events.append(_force_food(ev))
    return rc.dedupe(events)


def events_from_street_food(html: str) -> list:
    text = rc.clean(html)
    section = _between(text, "Nächste Termine", "Veranstalter")
    pattern = re.compile(
        r"(\d{1,2})\.\s*-\s*(\d{1,2})\.(\d{1,2})\.(20\d{2})\s+"
        r"Street Food Festival\s*-\s*(.*?)(?=\s+\d{1,2}\.\s*-\s*\d{1,2}\.\d{1,2}\.20\d{2}|$)",
        re.I,
    )
    events = []
    for start_day, end_day, month, year, location in pattern.findall(section):
        start = datetime(int(year), int(month), int(start_day))
        end = datetime(int(year), int(month), int(end_day))
        location = rc.clean(location)
        if "bad godesberg" in location.casefold():
            city, venue = "Bonn-Bad Godesberg", "Bad Godesberg"
        else:
            city, venue = rc.city_from_text(location, location), location
        ev = common.make_event(
            "Street Food Festival", start, end, venue, city,
            f"Street Food Festival in {location}.", _STREET_FOOD_URL,
            "Street Food Bonn", "street food markt festival genuss", 0.96,
            all_day=True,
        )
        if ev:
            events.append(_force_food(ev))
    return rc.dedupe(events)


def _events_from_schema_html(html: str, *, source: str, default_url: str,
                             default_city: str, category: str) -> list:
    events = []
    seen = set()
    for item in _deep_jsonld_events(html):
        start = _parse_schema_date(item.get("startDate"))
        if not start:
            continue
        status = str(item.get("eventStatus") or "")
        if status.endswith("EventCancelled"):
            continue
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        address = location.get("address")
        if not isinstance(address, dict):
            address = {"streetAddress": address} if address else {}
        city = rc.clean(str(address.get("addressLocality") or default_city))
        venue_parts = [location.get("name"), address.get("streetAddress"), address.get("postalCode")]
        address_text = " ".join(str(part).casefold() for part in venue_parts[1:] if part)
        if city and city.casefold() not in address_text:
            venue_parts.append(city)
        venue = ", ".join(rc.clean(str(part)) for part in venue_parts if part)
        offers = item.get("offers") or {}
        if isinstance(offers, list):
            offers = next((offer for offer in offers if isinstance(offer, dict)), {})
        if not isinstance(offers, dict):
            offers = {}
        link = str(item.get("url") or offers.get("url") or default_url)
        description = rc.clean(str(item.get("description") or ""))
        availability = str(offers.get("availability") or "")
        if availability.endswith("SoldOut"):
            description = _append_sentence(description, "Ausverkauft.")
        geo = location.get("geo") if isinstance(location.get("geo"), dict) else {}
        coords = None
        try:
            if geo.get("latitude") is not None and geo.get("longitude") is not None:
                coords = float(geo["latitude"]), float(geo["longitude"])
        except (TypeError, ValueError):
            coords = None
        key = (str(item.get("name") or ""), str(item.get("startDate") or ""), link)
        if key in seen:
            continue
        seen.add(key)
        ev = common.make_event(
            str(item.get("name") or ""), start,
            _parse_schema_date(item.get("endDate")) or start,
            venue, city, description, link, source, category, 0.99, coords=coords,
        )
        if ev:
            amount = offers.get("price")
            currency = offers.get("priceCurrency")
            if amount not in (None, ""):
                ev["price"] = _offer_price(amount, currency)
            events.append(_force_food(ev))
    return rc.dedupe(events)


def _parse_schema_date(value):
    """Normalize compact ISO offsets unsupported by Python 3.10."""
    normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", str(value or ""))
    return common.parse_iso_date(normalized)


def _deep_jsonld_events(html: str) -> list:
    found = []

    def walk(value):
        if isinstance(value, dict):
            types = value.get("@type") or []
            if isinstance(types, str):
                types = [types]
            if any(str(item_type).rsplit("/", 1)[-1].endswith("Event") for item_type in types):
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for raw in re.findall(
        r"<script[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
        html or "", re.S | re.I,
    ):
        try:
            walk(json.loads(raw.strip()))
        except (TypeError, ValueError):
            continue
    return found


def _craftquelle_detail(html: str) -> dict:
    text = rc.clean(html)
    start_match = re.search(
        r"Beginn:\s*(\d{1,2}\.(?:\d{1,2}\.|\s*[A-Za-zäöüÄÖÜ]+\s+)\d{2,4}),?\s*"
        r"(\d{1,2}:\d{2})",
        text,
        re.I,
    )
    start = None
    if start_match:
        date_value = common.parse_date(start_match.group(1)) or _parse_german_date(start_match.group(1))
        start = rc.with_time(date_value, start_match.group(2))
    end = start
    end_match = re.search(r"Ende\s*(?:ca\.)?\s*(\d{1,2}:\d{2})", text, re.I)
    if start and end_match:
        end = rc.with_time(start, end_match.group(1))
    venue = _match(r"Ort:\s*(.*?)(?=\s+(?:Biersommelier\b|Beginn:))", text)
    description = _between(text, "Beschreibung Beschreibung", "Leitung:")
    return {"start": start, "end": end, "venue": venue, "description": description}


def _ludwigs_detail(html: str, fallback_date: datetime) -> dict:
    main = _match(r"<main\b[^>]*>(.*?)</main>", html) or html
    text = rc.clean(main)
    timing = re.search(
        r"Am\s+(\d{1,2}\.\d{1,2}\.20\d{2})\s+ab\s+(\d{1,2})(?::(\d{2}))?\s+Uhr",
        text, re.I,
    )
    start = fallback_date
    if timing:
        start = rc.with_time(
            common.parse_date(timing.group(1)),
            f"{timing.group(2)}:{timing.group(3) or '00'}",
        )
    description = re.sub(r"\s*(?:Tickets sichern|Zurück)\s*(?:-->)?.*$", "", text, flags=re.I)
    return {"start": start, "end": start, "description": description}


def _reduettchen_detail(html: str) -> dict:
    text = rc.clean(html)
    time_match = re.search(r"(?:Beginn|Start)\s*:?[\s]*(\d{1,2})(?::(\d{2}))?\s*Uhr", text, re.I)
    price_match = re.search(
        r"(?:Preis\s*:?\s*)?(\d+(?:[,.]\d+)?)\s*(?:€|Euro|pro Person)", text, re.I,
    )
    description = _between(text, "Gourmet BBQ", "Kurfürstenallee 1") or text
    return {
        "time": (int(time_match.group(1)), int(time_match.group(2) or 0)) if time_match else None,
        "price": f"{price_match.group(1)} EUR" if price_match else "",
        "description": description,
    }


def _exact_reduettchen_dates(text: str) -> list:
    dates = []
    paired = re.search(
        r"(\d{1,2})\.\s*&\s*(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(20\d{2})", text,
    )
    if paired:
        first, second, month_name, year = paired.groups()
        month = _MONTHS.get(month_name.casefold())
        if month:
            return [datetime(int(year), month, int(first)), datetime(int(year), month, int(second))]
    match = re.search(r"\b(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(20\d{2})\b", text)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.casefold())
        if month:
            dates.append(datetime(int(year), month, int(day)))
    return dates


def _parse_german_date(text: str):
    match = re.search(r"\b(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(\d{2,4})\b", text or "")
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name.casefold())
    if not month:
        return None
    year_value = int(year) + 2000 if len(year) == 2 else int(year)
    try:
        return datetime(year_value, month, int(day))
    except ValueError:
        return None


def _date_from_href(href: str):
    match = re.search(r"(\d{1,2})-(\d{1,2})-(\d{2})(?:/|$)", href or "")
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return datetime(2000 + year, month, day)
    except ValueError:
        return None


def _force_food(event: dict) -> dict:
    event.update({
        "category_key": "food",
        "category_label": "Food & Genuss",
        "category_confidence": 1.0,
        "category_reason": "source:curated-food-calendar",
    })
    return event


def _safe_detail(fetcher, url: str, source: str) -> str:
    try:
        return fetcher(url)
    except Exception as exc:
        common.log_source_error(f"{source} detail", exc)
        return ""


def _price(text: str) -> str:
    value = rc.clean(text)
    if re.search(r"eintritt\s+frei|kostenlos", value, re.I):
        return "kostenlos"
    value = re.sub(r"\s*(?:p\.\s*P\.?|pro Person)\s*$", "", value, flags=re.I)
    value = value.replace("€", " EUR ")
    leading = re.match(r"\s*EUR\s*(\d+(?:[,.]\d+)?)", value, re.I)
    if leading:
        return f"{leading.group(1)} EUR"
    return re.sub(r"\s+", " ", value).strip()


def _offer_price(amount, currency) -> str:
    if isinstance(amount, float):
        value = f"{amount:.2f}"
    else:
        value = str(amount)
    return f"{value.replace('.', ',')} {currency or ''}".strip()


def _food_description(text: str) -> str:
    """Avoid a generic course-filter false positive for advanced tastings."""
    return re.sub(r"\bFortgeschrittene[n]?\b", "Kenner", rc.clean(text), flags=re.I)


def _in_window(date_value: datetime) -> bool:
    return common.TODAY <= date_value <= common.END_DATE.replace(
        hour=23, minute=59, second=59, microsecond=999999,
    )


def _append_sentence(text: str, sentence: str) -> str:
    text = rc.clean(text)
    return f"{text.rstrip('.')} . {sentence}".replace(" .", ".").strip()


def _between(text: str, start: str, end: str) -> str:
    lowered = text.lower()
    begin = lowered.find(start.lower())
    if begin < 0:
        return ""
    begin += len(start)
    finish = lowered.find(end.lower(), begin) if end else -1
    return text[begin:finish if finish >= 0 else None].strip()


def _match(pattern: str, text: str) -> str:
    match = re.search(pattern, text or "", re.S | re.I)
    return match.group(1).strip() if match else ""
