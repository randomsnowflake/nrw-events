"""
Bonn.de — the city's official event channels.

Fetchers, all reading bonn.de:
  fetch_events_json()      — the FULL official calendar as structured JSON
                             (≈5000 entries, category-tagged). Primary source.
                             Activity-only + date-windowed + venue-pinned.
  fetch_html()             — Veranstaltungskalender HTML listing (legacy fallback)
  fetch_rss()              — the same calendar as an RSS feed (legacy fallback)
  fetch_press_festivals()  — the annual "Veranstaltungsjahr" press release, which
                             lists district festivals / markets / Kirmes as <li>
                             items. This is the *live* replacement for the old
                             hardcoded district-festival table — no baked dates.
  fetch_sports()           — the public Sportveranstaltungen teaser page. This
                             exposes sport/active events that the primary JSON
                             source intentionally filters out.
"""

import json
import re
from datetime import datetime
from html import unescape

from .. import common

# Full official event calendar as structured JSON (≈5000 forward-looking entries,
# category-tagged). Far richer than the RSS/HTML listings, so this is now the
# primary Bonn source. We keep only genuine *activities* (see _ALLOW/_BLOCK) and
# only events whose date overlaps the configured window.
_EVENTS_JSON_URL = "https://www.bonn.de/citykey/events-json.php"

# Two open-data GeoJSON layers of cultural venues (point + name). Used to pin an
# event to its exact stage instead of the city centroid when the locationName
# matches. Source: Offene Daten Bonn (stadtplan.bonn.de).
_VENUE_GEOJSON_URLS = (
    "https://stadtplan.bonn.de/geojson?OD=4490",  # Schauspiel / Theater / Oper
    "https://stadtplan.bonn.de/geojson?OD=4489",  # Kleinkunst / Kabarett / Varieté
)

# Municipal category taxonomy → keep only real outings; drop civic/admin noise.
_ALLOW = {
    "Fest/Festival", "Musik/Konzert", "Kabarett", "Tanz", "Theater", "Ausstellungen",
    "Führungen/Rundgänge/Touren", "Tour", "Lesung", "Vorträge/Lesungen/Diskussionen",
    "Märkte/Messen", "Film/Medien", "Tag des offenen Denkmals", "Beethovenfest",
    "Weihnachtsmarkt", "Wissenschaftsnacht-Vorträge",
}
_FREE_ACTIVITY_ALLOW = {
    "Aktion/Workshop", "Bonn-Information", "Familien/Kinder", "Ferienaktion",
    "Kinder (0 bis 5 Jahre)", "Kinder (5 bis 12 Jahre)", "Kultur", "Sport",
    "Tourismus",
}
_FREE_EVENT_SCORE_FLOOR = 0.45
_BLOCK = {
    "Sprechstunde", "Sitzung", "Sitzungstermine Ausschüsse", "Sitzungstermine Bezirksvertretung",
    "Informations-Veranstaltung", "Tagungen/Kongresse", "Stadtverwaltung", "Fortbildungen",
    "Beratung", "Spendenaktion", "Online-Veranstaltung", "Bürger*innenbeteiligung",
    "Next Stop Job", "Bürger*innensprechstunde OB Déus",
}

_venue_points_cache = None
_detail_context_cache = {}


def _loads_event_items(raw: str):
    """Parse Bonn's event payload, tolerating server log lines appended after JSON.

    The city endpoint has occasionally emitted a valid JSON array prefix followed
    by PHP/SiteKit log text when its server is unhealthy. Keep the source useful
    by trimming only that clearly marked trailing log tail.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"(?<=\})(?:\r?\n)?\[\d{4}-\d{2}-\d{2}T", raw)
        if not (raw.lstrip().startswith("[") and match):
            raise
        return json.loads(raw[:match.start()] + "]")


def _venue_points() -> dict:
    """Lazy {venue_name_lower: (lat, lon)} from the two Bonn GeoJSON layers."""
    global _venue_points_cache
    if _venue_points_cache is not None:
        return _venue_points_cache
    pts: dict = {}
    for url in _VENUE_GEOJSON_URLS:
        try:
            data = json.loads(common.fetch_url(
                url,
                timeout=15,
                accept="application/geo+json,application/json,*/*;q=0.8",
                sec_fetch_mode="cors",
                sec_fetch_dest="empty",
            ))
        except Exception as e:
            common.log_source_error("Bonn venue GeoJSON", e)
            continue
        for feat in data.get("features", []):
            name = ((feat.get("properties") or {}).get("name") or "").strip().lower()
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if name and len(coords) == 2:
                lon, lat = coords[0], coords[1]
                pts[name] = (lat, lon)
    _venue_points_cache = pts
    return pts


def _parse_dt(value: str):
    """Parse the feed's 'YYYY-MM-DD HH:MM:SS' (or bare date) into a datetime."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _free_admission_price(item: dict, tags: set) -> str:
    """Return a normalized free-admission label from Bonn's category/prose."""
    if "Kostenlos" in tags:
        return "kostenlos"

    text = " ".join([
        item.get("title") or "",
        item.get("description") or "",
    ]).lower()
    if re.search(r"\b(eintritt frei|freier eintritt|kostenlos|kostenfreie?\w*|kostenlose?\w*)\b", text):
        return "kostenlos"
    return ""


def _strip_html(value: str) -> str:
    value = re.sub(r"<script\b.*?</script>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _parse_detail_context(html: str) -> dict:
    """Extract the useful bits that Bonn's JSON feed omits on sparse records."""
    context = {"description": "", "venue": "", "city": ""}

    text_parts = []
    intro = re.search(r'<div class="SP-ArticleHeader__intro[^"]*"[^>]*>(.*?)</div>', html, flags=re.S)
    if intro:
        text_parts.append(_strip_html(intro.group(1)))
    for block in re.findall(r'<div data-sp-table class="SP-Paragraph">(.*?)</div>', html, flags=re.S):
        text = _strip_html(block)
        if text:
            text_parts.append(text)
        if len(text_parts) >= 4:
            break

    seen = set()
    description_parts = []
    for text in text_parts:
        key = text.lower()
        if key and key not in seen:
            seen.add(key)
            description_parts.append(text)
    context["description"] = " ".join(description_parts)

    for raw_json in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, flags=re.S):
        try:
            data = json.loads(unescape(raw_json).strip())
        except Exception:
            continue
        nodes = data.get("@graph", []) if isinstance(data, dict) else []
        if isinstance(data, dict):
            nodes.append(data)
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") != "Event":
                continue
            locations = node.get("location") or []
            if isinstance(locations, dict):
                locations = [locations]
            if not locations:
                continue
            location = locations[0] or {}
            context["venue"] = (location.get("name") or "").strip()
            address = location.get("address") or {}
            if isinstance(address, dict):
                context["city"] = (address.get("addressLocality") or "").strip()
            return context

    return context


def _fetch_detail_context(link: str) -> dict:
    if not link or "bonn.de/veranstaltungskalender/" not in link:
        return {}
    if link in _detail_context_cache:
        return _detail_context_cache[link]
    try:
        html = common.fetch_url(
            link,
            timeout=15,
            accept="text/html,*/*;q=0.8",
            sec_fetch_mode="navigate",
            sec_fetch_dest="document",
        )
        context = _parse_detail_context(html)
    except Exception as e:
        common.log_source_error("Bonn.de detail", e)
        context = {}
    _detail_context_cache[link] = context
    return context


def _clean_free_title_prefix(title: str) -> str:
    """Remove Bonn.de editorial free-entry prefixes from display titles."""
    return re.sub(r"^\s*(?:kostenloser\s+eintritt|eintritt\s+frei)\s*:\s*", "", title or "", flags=re.I).strip()


def _apply_free_category_override(ev: dict, tags: set) -> dict:
    """Respect strong Bonn tags for free records admitted through free_allow."""
    if "Sport" in tags:
        return {
            **ev,
            "category_key": "sports",
            "category_label": "Sport & Bewegung",
            "category_confidence": max(ev.get("category_confidence", 0), 0.86),
            "category_reason": f"bonn-free-tag:Sport; {ev.get('category_reason', '')}".strip(),
        }
    if tags & {"Familien/Kinder", "Ferienaktion", "Kinder (0 bis 5 Jahre)", "Kinder (5 bis 12 Jahre)"}:
        return {
            **ev,
            "category_key": "kids",
            "category_label": "Familie & Kinder",
            "category_confidence": max(ev.get("category_confidence", 0), 0.86),
            "category_reason": f"bonn-free-tag:Familien/Kinder; {ev.get('category_reason', '')}".strip(),
        }
    return ev


def fetch_events_json() -> list:
    """Official Bonn events JSON → dated, activity-only, venue-pinned events."""
    source = "Bonn.de Events"
    try:
        items = _loads_event_items(common.fetch_url(
            _EVENTS_JSON_URL,
            timeout=25,
            accept="application/json,*/*;q=0.8",
            sec_fetch_mode="cors",
            sec_fetch_dest="empty",
        ))
    except Exception as e:
        fallback = _fetch_rss_events(source)
        if fallback:
            return fallback
        common.log_source_error(source, e)
        return []
    if not isinstance(items, list):
        items = items.get("events", []) if isinstance(items, dict) else []

    points = _venue_points()
    events = []
    for item in items:
        title = (item.get("title") or "").strip()
        if not title:
            continue

        tags = set(item.get("category") or [])
        allow = tags & _ALLOW
        price = _free_admission_price(item, tags)
        free_allow = (tags & _FREE_ACTIVITY_ALLOW) if price else set()
        if (not allow and not free_allow) or (tags & _BLOCK):
            continue

        start_dt = _parse_dt(item.get("startDate", ""))
        end_dt = _parse_dt(item.get("endDate", "")) or start_dt
        if not start_dt:
            continue  # no date → not a short-term plannable activity

        link = (item.get("link") or "").strip()
        description = (item.get("description") or "").strip()
        venue = (item.get("locationName") or "").strip()
        detail_context = {}
        if link and (not description or not venue):
            detail_context = _fetch_detail_context(link)
            description = description or detail_context.get("description", "")
            venue = venue or detail_context.get("venue", "")
        parts = [p.strip() for p in (item.get("locationAddress") or "").split(",") if p.strip()]
        town = re.sub(r"^\d{4,5}\s*", "", parts[-1]).strip() if parts else detail_context.get("city", "")
        city = town or "Bonn"

        # Only the time string and the venue-coordinate pin are Bonn-specific;
        # make_event owns the window/radius/date/dict/junk machinery.
        time_text = ""
        if item.get("hasStartTime") and (start_dt.hour or start_dt.minute):
            time_text = f"{start_dt:%H:%M}"
            if item.get("hasEndTime") and end_dt and (end_dt.hour or end_dt.minute):
                time_text += f"–{end_dt:%H:%M}"

        category_tags = allow or free_allow
        ev = common.make_event(
            title, start_dt, end_dt, venue, city, description, link,
            source, ", ".join(sorted(category_tags)), time_text=time_text,
            coords=points.get(venue.lower()))
        if ev:
            if free_allow and not allow:
                ev = _apply_free_category_override(ev, tags)
            if price:
                ev["price"] = price
                if free_allow:
                    ev["score"] = max(ev.get("score", 0), _FREE_EVENT_SCORE_FLOOR)
            events.append(ev)
    if len(events) < 20:
        events = _merge_fallback_events(events, _fetch_rss_events(source))
        events = _merge_fallback_events(events, _fetch_free_calendar_events(source))
    return events


_HTML_URL = "https://www.bonn.de/bonn-erleben/ausgehen-und-erleben/veranstaltungskalender.php"
_SPORTS_URL = "https://www.bonn.de/bonn-erleben/aktiv-und-unterwegs/sportveranstaltungen.php"
_RSS_URL = (_HTML_URL + "?sp%3Aout=rss&sp%3Acmp=search-1-0-searchResult&action=submit")
# Annual press release. The slug embeds the year; we build it dynamically so the
# source keeps working in future years with no code change (no dates hardcoded).
_PRESS_URL_TEMPLATE = (
    "https://www.bonn.de/pressemitteilungen/dezember/"
    "abwechslungsreiches-veranstaltungsjahr-{year}-in-bonn.php"
)


def fetch_html() -> list:
    source = "Bonn.de"
    try:
        html = common.fetch_url(_HTML_URL)
        events = []
        pattern = r'<a[^>]*href="(/veranstaltungskalender/[^"]+?)"[^>]*>(.*?)</a>'
        for href, text in re.findall(pattern, html, re.DOTALL):
            clean = unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip())
            if "speichern" in clean.lower() or len(clean) < 10:
                continue

            cat_match = re.match(r"^([\w/|]+(?:\s*\|\s*[\w/]+)*)\s*", clean)
            category = cat_match.group(1) if cat_match else ""
            dates = re.findall(r"(\d{2}\.\d{2}\.\d{4})", clean)

            title_part = clean[len(category):].strip() if category else clean
            title_part = re.sub(r"\d{2}\.\d{2}\.\d{4}\s*\d{0,2}:?\d{0,2}\s*(?:Uhr)?\s*,?\s*", "", title_part)
            title_part = re.sub(r"[\xa0 ]", " ", title_part)
            title_part = re.sub(r"^[\s.,;]*\.{3}\s*", "", title_part)
            title_part = re.sub(r"^\.\.\.\s*", "", title_part).strip()

            for sep in [" Bei der ", " Die ", " Spannende ", " Im Rahmen ", " Informieren ",
                        " Auf dieser ", " Eine ", " Das ", " Monatlicher "]:
                if sep in title_part and len(title_part.split(sep)[0]) > 10:
                    title_part = title_part.split(sep)[0]
                    break
            if len(title_part) > 80:
                for brk in [" - ", " – ", " | ", ". "]:
                    if brk in title_part[:80]:
                        break
                else:
                    title_part = title_part[:80]
            if not title_part or len(title_part) < 3:
                continue

            if dates and not any(common.in_date_range(d) for d in dates):
                continue

            full_text = f"{category} {title_part}"
            events.append({
                "title": title_part[:120],
                "date": dates[0] if dates else "",
                "time": "", "venue": "", "city": "Bonn", "description": "", "price": "",
                "link": f"https://www.bonn.de{href}",
                "distance_km": 0,
                "score": round(common.distance_score(0) * common.category_score(full_text), 2),
                "source": source,
                "category": category,
            })
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _calendar_search_url(page: int = 1) -> str:
    params = [
        ("sp:categories[1530][]", "326135"),  # Zielgruppe → Kostenlos
        ("sp:categories[1530][]", "__last__"),
        ("sp:dateFrom[]", common.TODAY.strftime("%Y-%m-%d")),
        ("sp:dateTo[]", common.END_DATE.strftime("%Y-%m-%d")),
        ("action", "submit"),
    ]
    if page > 1:
        params.append(("sp:page[search-1.form][0]", str(page)))
    return _HTML_URL + "?" + common.urllib.parse.urlencode(params)


def _pagination_max(html: str) -> int:
    match = re.search(r"&quot;max&quot;:(\d+)", html)
    if match:
        return max(1, int(match.group(1)))
    match = re.search(r'"max"\s*:\s*(\d+)', unescape(html))
    return max(1, int(match.group(1))) if match else 1


def _split_tags(value: str) -> set:
    return {part.strip() for part in re.split(r"\s*(?:,|\|)\s*", value or "") if part.strip()}


def _free_listing_events_from_html(html: str, source: str) -> list:
    events, seen = [], set()
    for m in re.finditer(r'<article class="SP-Teaser\b.*?</article>', html, re.S | re.I):
        body = m.group(0)
        href_m = re.search(r'<a[^>]+class="[^"]*SP-Teaser__inner[^"]*"[^>]+href="([^"]+)"', body, re.S | re.I)
        title_m = re.search(r'<h1[^>]+class="[^"]*SP-Teaser__headline[^"]*"[^>]*>(.*?)</h1>', body, re.S | re.I)
        cat_m = re.search(r'<span[^>]+class="[^"]*SP-Kicker__text[^"]*"[^>]*>(.*?)</span>', body, re.S | re.I)
        if not (href_m and title_m):
            continue
        href = href_m.group(1).split("?", 1)[0]
        if "/veranstaltungskalender/veranstaltungen/hauptkalender/extern/" not in href:
            continue

        raw_title = common.clean_html(title_m.group(1))
        title = _clean_free_title_prefix(raw_title)
        category = common.clean_html(cat_m.group(1) if cat_m else "")
        tags = _split_tags(category)
        allow = tags & _ALLOW
        free_allow = tags & _FREE_ACTIVITY_ALLOW
        if (not allow and not free_allow) or (tags & _BLOCK):
            continue

        link = common.urllib.parse.urljoin("https://www.bonn.de", href)
        date_matches = re.findall(
            r'<span>\s*<span[^>]+class="[^"]*SP-Scheduling__date[^"]*"[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*</span>'
            r'(?:\s*<span[^>]+class="[^"]*SP-Scheduling__time[^"]*"[^>]*>\s*([^<]*?)\s*</span>)?\s*</span>',
            body,
            re.S | re.I,
        )
        for date_text, time_raw in date_matches:
            start = common.parse_date(date_text)
            if not start or not (common.TODAY <= start <= common.END_DATE):
                continue
            time_text = common.clean_html(time_raw)
            time_match = re.search(r"(\d{1,2}):(\d{2})", time_text)
            if time_match:
                start = start.replace(hour=int(time_match.group(1)), minute=int(time_match.group(2)))
                time_text = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
            else:
                time_text = ""

            key = (title.lower(), start.strftime("%Y-%m-%d"), link)
            if key in seen:
                continue
            seen.add(key)
            ev = common.make_event(
                title, start, start, "", "Bonn", raw_title, link,
                source, ", ".join(sorted(tags | {"Kostenlos"})), trust=0.86, time_text=time_text,
            )
            if ev:
                ev["price"] = "kostenlos"
                if free_allow and not allow:
                    ev = _apply_free_category_override(ev, tags)
                    ev["score"] = max(ev.get("score", 0), _FREE_EVENT_SCORE_FLOOR)
                events.append(ev)
    return events


def _fetch_free_calendar_events(source: str = "Bonn.de Events") -> list:
    """Fallback: crawl Bonn's free-category listing when the JSON feed is broken.

    The public JSON endpoint occasionally truncates before current-day entries.
    The server-rendered calendar still exposes the "Kostenlos" category via
    paginated SP-Teaser cards, which is enough to recover free-entry events.
    """
    try:
        first = common.fetch_url(_calendar_search_url(), timeout=25)
    except Exception as e:
        common.log_source_error(f"{source} free calendar fallback", e)
        return []

    events = _free_listing_events_from_html(first, source)
    max_page = min(_pagination_max(first), 20)
    for page in range(2, max_page + 1):
        try:
            events = _merge_fallback_events(
                events,
                _free_listing_events_from_html(common.fetch_url(_calendar_search_url(page), timeout=25), source),
            )
        except Exception as e:
            common.log_source_error(f"{source} free calendar fallback page {page}", e)
            continue
    return events


def _parse_sport_time(text: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def events_from_sport_teasers(html: str) -> list:
    """Parse the Bonn.de Sportveranstaltungen teaser list into dated events."""
    source = "Bonn.de Sports"
    events, seen = [], set()
    for m in re.finditer(r'<article class="SP-Teaser\b.*?</article>', html, re.S | re.I):
        body = m.group(0)
        href_m = re.search(r'<a[^>]+class="[^"]*SP-Teaser__inner[^"]*"[^>]+href="([^"]+)"', body, re.S | re.I)
        title_m = re.search(r'<h1[^>]+class="[^"]*SP-Teaser__headline[^"]*"[^>]*>(.*?)</h1>', body, re.S | re.I)
        cat_m = re.search(r'<span[^>]+class="[^"]*SP-Kicker__text[^"]*"[^>]*>(.*?)</span>', body, re.S | re.I)
        if not (href_m and title_m):
            continue
        title = common.clean_html(title_m.group(1))
        category = common.clean_html(cat_m.group(1) if cat_m else "Sport") or "Sport"
        href = href_m.group(1).split("?", 1)[0]
        link = common.urllib.parse.urljoin("https://www.bonn.de", href)
        for date_text, time_raw in re.findall(
            r'<span[^>]+class="[^"]*SP-Scheduling__date[^"]*"[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s*</span>'
            r'(?:\s*<span[^>]+class="[^"]*SP-Scheduling__time[^"]*"[^>]*>\s*([^<]*?)\s*</span>)?',
            body, re.S | re.I,
        ):
            start = common.parse_date(date_text)
            time_text = _parse_sport_time(common.clean_html(time_raw))
            if start and time_text:
                hour, minute = map(int, time_text.split(":"))
                start = start.replace(hour=hour, minute=minute)
            key = (title.lower(), start.strftime("%Y-%m-%d") if start else date_text, time_text)
            if key in seen:
                continue
            seen.add(key)
            ev = common.make_event(
                title, start, start, "", "Bonn", "", link,
                source, category, trust=0.8, time_text=time_text,
            )
            if ev:
                events.append(ev)
    return events


def fetch_sports() -> list:
    source = "Bonn.de Sports"
    try:
        return events_from_sport_teasers(common.fetch_url(_SPORTS_URL, timeout=20))
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _fetch_rss_events(source: str = "Bonn.de RSS") -> list:
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(common.fetch_url(
            _RSS_URL,
            accept="application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
            sec_fetch_mode="no-cors",
            sec_fetch_dest="empty",
        ))
        events = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            pub_date = (item.findtext("pubDate") or "").strip()
            desc = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc_text = unescape(re.sub(r"<[^>]+>", "", desc)) if desc else ""
            ev = common.make_event(
                unescape(title),
                common.parse_date(pub_date),
                common.parse_date(pub_date),
                "",
                "Bonn",
                desc_text,
                link,
                source,
                "official calendar rss bonn",
                trust=0.76,
            )
            if ev and ev.get("category_key") == "other":
                detail_context = _fetch_detail_context(link)
                detail_description = detail_context.get("description") or ""
                if detail_description:
                    enriched = common.make_event(
                        unescape(title),
                        common.parse_date(pub_date),
                        common.parse_date(pub_date),
                        detail_context.get("venue", ""),
                        detail_context.get("city") or "Bonn",
                        detail_description,
                        link,
                        source,
                        "official calendar rss bonn",
                        trust=0.76,
                    )
                    if enriched:
                        ev = enriched
            if ev:
                price = _free_admission_price({"title": title, "description": desc}, set())
                if price:
                    ev["price"] = price
                events.append(ev)
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _merge_fallback_events(primary: list, fallback: list) -> list:
    seen = {
        (event.get("link") or "", event.get("title") or "", event.get("date") or "")
        for event in primary
    }
    merged = list(primary)
    for event in fallback:
        key = (event.get("link") or "", event.get("title") or "", event.get("date") or "")
        if key in seen:
            continue
        merged.append(event)
        seen.add(key)
    return merged


def fetch_rss() -> list:
    return _fetch_rss_events("Bonn.de RSS")


def fetch_press_festivals() -> list:
    """Parse the annual Bonn 'Veranstaltungsjahr' press release for district festivals.

    Each <li> looks like: "<name>, <venue...>, <date>, <date>, … <year>".
    We extract the name + every in-window date and emit one event per date. This
    surfaces Stadtteilfeste / Kirmes / markets that never reach the clean APIs —
    fully live, no event names or dates hardcoded in the script.
    """
    source = "Bonn district festivals"
    # Try this year; from October onward also try next year's edition (published early).
    years = [common.TODAY.year]
    if common.TODAY.month >= 10:
        years.append(common.TODAY.year + 1)

    events = []
    for year in years:
        url = _PRESS_URL_TEMPLATE.format(year=year)
        try:
            html = common.fetch_url(url, timeout=20)
        except Exception:
            continue  # edition not published / slug changed → just skip
        for li in re.findall(r"<li>(.*?)</li>", html, re.S):
            text = common.clean_html(li)
            if len(text) < 6:
                continue
            dates = []
            for m in re.finditer(
                r"(\d{1,2})\.\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s*(20\d{2})?",
                text,
            ):
                day, mon, yr = m.groups()
                try:
                    dates.append(datetime(int(yr or year), common.MONTH_DE[mon.lower()], int(day)))
                except (ValueError, KeyError):
                    continue
            in_window = [d for d in dates if common.TODAY <= d <= common.END_DATE]
            if not in_window:
                continue
            title = re.split(r",", text)[0].strip()
            if len(title) < 3:
                continue
            city = common.guess_city_from_text(text) or "Bonn"
            for d in sorted(set(in_window)):
                ev = common.make_event(
                    title, d, d, "", city, text[:240], url, source,
                    "stadtteilfest market kirmes outdoor local", 1.0,
                )
                if ev:
                    events.append(ev)
    return events
