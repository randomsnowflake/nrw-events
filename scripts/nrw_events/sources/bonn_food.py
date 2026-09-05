"""Primary food and drink event calendars in and around Bonn."""

import re
import urllib.parse
from datetime import datetime, timedelta

from .. import common
from ..dates import MONTH_ALL
from ..health import SourceFetchResult
from . import regional_common as rc

_CRAFTQUELLE_URL = "https://craft-quelle.de/neue-tasting-termine/"
_BFF_URL = "https://bff-bonn.com/kulinarische-highlights-bonn"
_VOMFASS_URL = "https://www.vomfass.de/pages/tastings"
_BIERTASTING_URL = "https://www.biertasting-bonn.de/"
_LUDWIGS_URL = "https://www.ludwigs-bonn.de/veranstaltungen"
_REDUETTCHEN_URL = "https://reduettchen.de/events/"
_STREET_FOOD_URL = "https://www.street-food-bonn.de/"
# Same organiser (WEvent UG), same Contao layout, same "Nächste Termine" block:
# one parser reads both landing pages and the shared dedupe collapses the
# festivals both of them advertise.
_STREET_FOOD_SIEGBURG_URL = "https://www.streetfood-siegburg.de/"
_ORIGINAL_STREET_FOOD_URL = "https://street-food-festival.de/bonn"
_CHOCO_DEALER_URL = "https://choco-dealer.com/EVENTS/"
_VOMFASS_ALLOWED_HOSTS = ("www.vomfass.de",)

def fetch_craftquelle() -> list:
    return rc.fetch_html_events(
        "Craftquelle Bonn",
        _CRAFTQUELLE_URL,
        lambda html: events_from_craftquelle(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="craftquelle-bonn", timeout=20),
        ),
     source_id="craftquelle-bonn")


def fetch_bff() -> list:
    return rc.fetch_html_events("BFF Bonner Schifffahrt", _BFF_URL, events_from_bff, source_id="bff-bonner-schifffahrt")


def fetch_vomfass() -> list | SourceFetchResult:
    if common.runtime_window().start.weekday() != 0:
        return SourceFetchResult.scheduled_skip(
            "weekly refresh runs on Mondays; retaining unexpired events"
        )
    return rc.fetch_html_events(
        "vomFASS Bonn",
        _VOMFASS_URL,
        lambda html: events_from_vomfass(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="vomfass-bonn", timeout=20,
                brightdata=True,
                allowed_hosts=_VOMFASS_ALLOWED_HOSTS,
                required_body_markers=("application/ld+json",)),
        ),
        fetcher=_fetch_vomfass_listing,
     source_id="vomfass-bonn")


def _fetch_vomfass_listing(url: str, timeout: int) -> str:
    def fetch_page(page_url: str) -> str:
        return common.fetch_url_with_brightdata(
            page_url,
            timeout=timeout,
            allowed_hosts=_VOMFASS_ALLOWED_HOSTS,
            required_body_markers=("data-event-card",),
        )

    first_page = fetch_page(url)
    results_tag = _match(r"(<[^>]+data-ef-results\b[^>]*>)", first_page)
    section_id = _match(r"data-ef-section-id=['\"]([A-Za-z0-9_-]+)", results_tag)
    page_param = _match(r"data-ef-page-param=['\"]([A-Za-z0-9_-]+)", results_tag)
    current_page = _match(r"data-ef-page=['\"](\d+)", results_tag)
    total_pages = _match(r"data-ef-pages=['\"](\d+)", results_tag)
    if not all((section_id, page_param, current_page, total_pages)):
        return first_page

    current_page_number = int(current_page)
    total_page_count = int(total_pages)
    if total_page_count > 20:
        raise RuntimeError(f"vomFASS listing reported an implausible {total_page_count} pages")

    base_url = urllib.parse.urlunsplit((*urllib.parse.urlsplit(url)[:3], "", ""))
    pages = [first_page]
    for page_number in range(1, total_page_count + 1):
        if page_number == current_page_number:
            continue
        query = urllib.parse.urlencode({"section_id": section_id, page_param: page_number})
        pages.append(fetch_page(f"{base_url}?{query}"))
    return "\n".join(pages)


def fetch_biertasting() -> list:
    return rc.fetch_html_events("Biertasting Bonn", _BIERTASTING_URL, events_from_biertasting, source_id="biertasting-bonn")


def fetch_ludwigs() -> list:
    return rc.fetch_html_events(
        "Ludwig's Bonn",
        _LUDWIGS_URL,
        lambda html: events_from_ludwigs(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="ludwigs-bonn", timeout=20),
        ),
 source_id="ludwig-s-bonn")


def fetch_reduettchen() -> list:
    return rc.fetch_html_events(
        "Redüttchen",
        _REDUETTCHEN_URL,
        lambda html: events_from_reduettchen(
            html,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="reduettchen", timeout=20),
        ),
 source_id="red-ttchen")


def fetch_street_food() -> list:
    events = []
    for page_url in (_STREET_FOOD_URL, _STREET_FOOD_SIEGBURG_URL):
        events.extend(rc.fetch_html_events(
            "Street Food Bonn",
            page_url,
            lambda html, source_url=page_url: events_from_street_food(
                html, source_url=source_url,
            ),
            empty_is_healthy=(
                _street_food_bonn_expected_empty if page_url == _STREET_FOOD_URL else False
            ),
         source_id="street-food-bonn"))
    return rc.dedupe(events)


def fetch_original_street_food() -> list:
    return rc.fetch_html_events(
        "Street Food Festival Original", _ORIGINAL_STREET_FOOD_URL,
        events_from_original_street_food,
     source_id="street-food-festival-original")


def fetch_choco_dealer() -> list:
    return rc.fetch_html_events("Choco Dealer", _CHOCO_DEALER_URL, events_from_choco_dealer, source_id="choco-dealer")


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
    schema_events = _events_from_schema_html(
        html, source="BFF Bonner Schifffahrt", default_url=_BFF_URL,
        default_city="Bonn", category="kulinarische schifffahrt dinner brunch genuss",
    )
    if schema_events:
        return schema_events

    # Some booking-widget responses publish no schema.org Event nodes. Their
    # visible timetable is still structured and includes the exact date, start
    # time, departure point, description and booking URL, so use that rather
    # than treating a populated official calendar as empty.
    events = []
    blocks = re.findall(
        r"<div class=['\"][^'\"]*\bblock-timetable\b[^'\"]*['\"][^>]*>.*?<hr class=['\"]divider['\"][^>]*>",
        html or "",
        re.S | re.I,
    )
    for block in blocks:
        date_text = rc.first_group_clean(
            r"<span class=['\"]datum['\"]>(.*?)</span>", block,
        )
        time_text = rc.first_group_clean(
            r"<span class=['\"]uhrzeit['\"]>(.*?)</span>", block,
        )
        title = rc.first_group_clean(
            r"<div class=['\"]linie_bezeichnung['\"]>(.*?)</div>", block,
        )
        venue = rc.first_group_clean(
            r"<div class=['\"]abfahrt_station['\"]>(.*?)</div>", block,
        )
        description = rc.first_group_clean(
            r"<div class=['\"]infotext['\"]>(.*?)</div>", block,
        )
        link = _match(r"<div class=['\"]weiterlesen_link['\"]>.*?href=['\"]([^'\"]+)", block)
        start = common.parse_date(date_text)
        if not (start and title):
            continue
        start = rc.with_time(start, time_text)
        event = common.make_event(
            title, start, None, venue, "Bonn",
            description or common.factual_event_description(
                title, date_value=start, time_text=time_text, venue=venue, city="Bonn",
            ),
            rc.abs_url(_BFF_URL, link) if link else _BFF_URL,
            "BFF Bonner Schifffahrt",
            "kulinarische schifffahrt dinner brunch genuss",
            0.98,
            time_text,
        )
        if event:
            events.append(_force_food(event))
    return rc.dedupe_occurrences(events)


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
        month = MONTH_ALL[month_name.casefold().rstrip(".")]
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


def events_from_street_food(html: str, *, source_url: str = _STREET_FOOD_URL) -> list:
    text = rc.clean(html)
    # Both landing pages of the organiser are parsed in one pass, so the term
    # list is bounded by the next date or the imprint block instead of by a
    # single "Nächste Termine … Veranstalter" section. The Bonn page separates
    # the location with a dash, the Siegburg page does not.
    pattern = re.compile(
        r"(\d{1,2})\.\s*-\s*(\d{1,2})\.(\d{1,2})\.(20\d{2})\s+"
        r"Street Food Festival\s*-?\s*(.*?)"
        r"(?=\s+\d{1,2}\.\s*-\s*\d{1,2}\.\d{1,2}\.20\d{2}|\s+Veranstalter\b|$)",
        re.I,
    )
    events = []
    for start_day, end_day, month, year, raw_location in pattern.findall(text):
        start = datetime(int(year), int(month), int(start_day))
        end = datetime(int(year), int(month), int(end_day))
        location = rc.clean(raw_location)
        if "bad godesberg" in location.casefold():
            city, venue = "Bonn-Bad Godesberg", "Bad Godesberg"
        else:
            city, venue = rc.city_from_text(location, location), location
        ev = common.make_event(
            "Street Food Festival", start, end, venue, city,
            f"Street Food Festival in {location}.", source_url,
            "Street Food Bonn", "street food markt festival genuss", 0.96,
            all_day=True,
        )
        if ev:
            events.append(_force_food(ev))
    return rc.dedupe(events)


def _street_food_bonn_expected_empty(html: str) -> bool:
    """Recognize an official Bonn landing page with no current Bonn edition."""
    hero_date = rc.first_group_clean(
        r"<div[^>]+class=['\"][^'\"]*\bsfdatum\b[^'\"]*['\"][^>]*>(.*?)</div>",
        html or "",
    )
    if re.search(r"\breturning\s+in\s+20\d{2}\b", hero_date, re.I):
        return True
    advertised_dates = common.extract_dates(hero_date)
    return bool(advertised_dates) and not any(
        common.window_contains(date_value, date_value)
        for date_value in advertised_dates
    )


def events_from_original_street_food(html: str) -> list:
    """Read the festival dates from the visible page, never from its JSON-LD.

    The page ships a ``schema.org/FoodEvent`` block, but it is unmaintained: its
    ``startDate`` still points at a past edition and its time component is not
    valid ISO 8601. Only ``name``, ``description`` and ``location`` are taken
    from it; without a readable date in the page body nothing is published and
    the source reports an empty parse.
    """
    item = next((entry for entry in _deep_jsonld_events(html)), {})
    visible_html = _without_scripts(html)
    title = rc.clean(str(item.get("name") or "")) or rc.first_group_clean(
        r"<h[1-3][^>]*>\s*([^<]*Street Food Festival in Bonn[^<]*)</h[1-3]>",
        visible_html,
    )
    if not title:
        return []
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    city = rc.clean(str(address.get("addressLocality") or "Bonn"))
    venue = ", ".join(
        rc.clean(str(part)) for part in
        (location.get("name"), address.get("streetAddress"), address.get("postalCode"))
        if part
    )
    if not venue:
        venue = rc.first_group_clean(
            r"<h[1-3][^>]*>\s*LOCATION:\s*(.*?)</h[1-3]>", visible_html,
        )
    description = rc.clean(str(item.get("description") or "")) or (
        "Das Original Street Food Festival in Bonn."
    )
    events = []
    for start, end in _spelled_date_ranges(rc.clean(visible_html)):
        ev = common.make_event(
            title, start, end, venue, city, description,
            _ORIGINAL_STREET_FOOD_URL, "Street Food Festival Original",
            "street food markt festival genuss", 0.9, all_day=True,
        )
        if ev:
            events.append(_force_food(ev))
    return rc.dedupe(events)


def events_from_choco_dealer(html: str) -> list:
    events = []
    for card in re.split(r"<div[^>]*\bevents-card\b", html or "")[1:]:
        link = _match(r"href=['\"]([^'\"]*\bslotId=[^'\"]+)['\"]", card)
        title = rc.clean(_match(r"netzp-events-title[^'\"]*['\"][^>]*>(.*?)</div>", card))
        stamp = re.search(
            r"(\d{1,2})\.(\d{1,2})\.(\d{2}),\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", card,
        )
        if not (link and title and stamp):
            continue
        absolute_link = rc.abs_url(_CHOCO_DEALER_URL, link)
        booking_path = urllib.parse.unquote(
            urllib.parse.urlsplit(absolute_link).path
        ).replace("-", " ")
        if "tasting" not in title.casefold():
            tasting_kind = _match(
                r"\b((?:wein\s+)?schokoladen\s+tasting)\b",
                booking_path,
            )
            if tasting_kind:
                title = f"{tasting_kind.title()}: {title}"
        day, month, year, start_time, end_time = stamp.groups()
        try:
            start = datetime(2000 + int(year), int(month), int(day))
        except ValueError:
            continue
        start = rc.with_time(start, start_time)
        # ``<b\b`` would also match ``<br>`` and swallow the whole card as a venue.
        venue_match = re.search(r"<b(?:\s[^>]*)?>(.*?)</b>\s*\|\s*([^<]*)", card, re.S)
        venue = ", ".join(rc.clean(part) for part in venue_match.groups()) if venue_match else ""
        description = re.sub(
            r"\s*\.{3}$", "",
            rc.clean(_match(r"card-text lead[^'\"]*['\"][^>]*>(.*?)</div>", card)),
        )
        ev = common.make_event(
            title, start, rc.with_time(start, end_time), venue, "Bonn-Bad Godesberg",
            description, absolute_link, "Choco Dealer",
            "schokolade tasting verkostung genuss", 0.97,
        )
        if ev:
            events.append(_force_food(ev))
    return rc.dedupe(events)


def _spelled_date_ranges(text: str) -> list:
    """Parse ``02. - 05. Oktober 2026`` and ``30. September - 03. Oktober 2026``."""
    ranges = []
    pattern = re.compile(
        r"\b(\d{1,2})\.\s*(?:([A-Za-zäöüÄÖÜ]+)\.?\s+)?[-–—]\s*"
        r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\.?\s+(20\d{2})\b"
    )
    for start_day, start_month_name, end_day, end_month_name, year in pattern.findall(text):
        end_month = MONTH_ALL.get(end_month_name.casefold().rstrip("."))
        start_month = MONTH_ALL.get((start_month_name or end_month_name).casefold().rstrip("."))
        if not (start_month and end_month):
            continue
        try:
            end = datetime(int(year), end_month, int(end_day))
            # A range that wraps the new year starts in the preceding year.
            start = datetime(int(year) - (start_month > end_month), start_month, int(start_day))
        except ValueError:
            continue
        ranges.append((start, end))
    return ranges


def _without_scripts(html: str) -> str:
    return re.sub(r"<script\b.*?</script>", " ", html or "", flags=re.S | re.I)


def _events_from_schema_html(html: str, *, source: str, default_url: str,
                             default_city: str, category: str) -> list:
    events = []
    seen = set()
    for item in _deep_jsonld_events(html):
        start = _parse_schema_date(item.get("startDate"))
        if not start:
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
            if schema_status := common.jsonld_event_status(item.get("eventStatus")):
                ev["status"] = schema_status
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
    return common.jsonld_event_items(html)


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
        r"(\d{1,2})\.\s*&\s*(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\.?\s+(20\d{2})", text,
    )
    if paired:
        first, second, month_name, year = paired.groups()
        month = MONTH_ALL.get(month_name.casefold().rstrip("."))
        if month:
            return [datetime(int(year), month, int(first)), datetime(int(year), month, int(second))]
    match = re.search(r"\b(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\.?\s+(20\d{2})\b", text)
    if match:
        day, month_name, year = match.groups()
        month = MONTH_ALL.get(month_name.casefold().rstrip("."))
        if month:
            dates.append(datetime(int(year), month, int(day)))
    return dates


def _parse_german_date(text: str):
    match = re.search(r"\b(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\.?\s+(\d{2,4})\b", text or "")
    if not match:
        return None
    day, month_name, year = match.groups()
    month = MONTH_ALL.get(month_name.casefold().rstrip("."))
    if not month:
        return None
    year_value = f"20{year}" if len(year) == 2 else year
    return common.parse_date(f"{day}.{month}.{year_value}")


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
    free_price = common.infer_free_admission_price("", value)
    if free_price:
        return free_price
    value = re.sub(r"\s*(?:p\.\s*P\.?|pro Person)\s*$", "", value, flags=re.I)
    value = value.replace("€", " EUR ")
    leading = re.match(r"\s*EUR\s*(\d+(?:[,.]\d+)?)", value, re.I)
    if leading:
        return f"{leading.group(1)} EUR"
    return re.sub(r"\s+", " ", value).strip()


def _offer_price(amount, currency) -> str:
    value = f"{amount:.2f}" if isinstance(amount, float) else str(amount)
    return f"{value.replace('.', ',')} {currency or ''}".strip()


def _food_description(text: str) -> str:
    """Avoid a generic course-filter false positive for advanced tastings."""
    return re.sub(r"\bFortgeschrittene[n]?\b", "Kenner", rc.clean(text), flags=re.I)


def _in_window(date_value: datetime) -> bool:
    return common.window_contains(date_value)


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
