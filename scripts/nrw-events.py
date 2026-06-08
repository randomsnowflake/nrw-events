#!/usr/bin/env python3
"""
NRW Event Discovery — Weekend Planner
Fetches events from multiple sources within 75km of Bonn,
scores them by distance + category preference, and outputs
a structured weekend report.

Sources:
  Tier 1 (APIs):    Köln Open Data, Bonn.de HTML
  Tier 2 (Structured): Rheinauen-Flohmarkt recurrence, Bonn.jetzt, Songkick (concerts), Bundeskunsthalle
  Tier 3 (Search):  Exa Search, optional Grok Search
"""

import json
import math
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from html import unescape

# ── Config ──────────────────────────────────────────────────────────
BONN_LAT, BONN_LON = 50.7374, 7.0982
MAX_RADIUS_KM = 75
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
EXA_API_KEY = os.environ.get("EXA_API_KEY", "")

# Days to look ahead (default: Fri-Sun = 3 days)
DAYS_AHEAD = int(sys.argv[1]) if len(sys.argv) > 1 else 3

TODAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
END_DATE = TODAY + timedelta(days=max(DAYS_AHEAD - 1, 0))

# Category preference weights (higher = ranked more prominently). These are
# opinionated defaults skewed toward culture + outdoor + nightlife — tune to taste.
CATEGORY_WEIGHT = {
    "concert": 1.5,
    "musik": 1.5,
    "music": 1.5,
    "electronic": 1.8,
    "techno": 1.8,
    "exhibition": 1.4,
    "ausstellung": 1.4,
    "museum": 1.3,
    "architecture": 1.6,
    "art": 1.3,
    "kunst": 1.3,
    "outdoor": 1.35,
    "hiking": 1.35,
    "wandern": 1.35,
    "wanderung": 1.35,
    "geführte wanderung": 1.45,
    "führung": 1.25,
    "tour": 1.2,
    "nature": 1.25,
    "natur": 1.35,
    "rhein": 1.15,
    "drachenfels": 1.4,
    "petersberg": 1.3,
    "oelberg": 1.3,
    "ölberg": 1.3,
    "heisterbach": 1.25,
    "market": 1.1,
    "markt": 1.1,
    "festival": 1.4,
    "theater": 1.0,
    "comedy": 1.0,
    "lecture": 1.1,
    "vortrag": 1.1,
    "nightlife": 1.3,
    "party": 1.2,
    "food": 1.2,
    "wein": 1.45,
    "wine": 1.45,
    "winzer": 1.4,
    "wanderung": 1.35,
    "weinwanderung": 1.55,
    "walk": 1.25,
    "genuss": 1.25,
    "stadtteilfest": 1.45,
    "straßenfest": 1.45,
    "strassenfest": 1.45,
    "dorffest": 1.35,
    "kirmes": 1.25,
    "viertel": 1.2,
    "meile": 1.25,
    "poppelsdorf": 1.35,
    "endenich": 1.25,
    "beuel": 1.2,
    "bad godesberg": 1.2,
    "siegbirge": 1.25,
    "siebengebirge": 1.35,
    "kottenforst": 1.3,
    "natur": 1.25,
    "ahrtal": 1.35,
    "königswinter": 1.3,
    "koenigswinter": 1.3,
    "andernach": 1.2,
    "bad honnef": 1.2,
    "linz": 1.15,
    "remagen": 1.2,
    "rolandseck": 1.2,
    "unkel": 1.15,
    "altenahr": 1.2,
    "dernau": 1.25,
    "mayschoss": 1.25,
    "regional": 1.15,
    "film": 1.0,
    "kids": 0.2,       # deprioritize
    "kinder": 0.2,
    "family": 0.3,
    "familie": 0.3,
    "sport": 0.5,
    "workshop": 0.7,
    "reading": 0.4,    # children's reading events
    "vorlesen": 0.3,
    "basteln": 0.2,
}

# Known venue coordinates (saves geocoding)
VENUE_COORDS = {
    "bonn": (50.7374, 7.0982),
    "köln": (50.9375, 6.9603),
    "koeln": (50.9375, 6.9603),
    "cologne": (50.9375, 6.9603),
    "siegburg": (50.7972, 7.2028),
    "troisdorf": (50.8157, 7.1554),
    "königswinter": (50.6741, 7.1844),
    "bad honnef": (50.6452, 7.2278),
    "sankt augustin": (50.7705, 7.1867),
    "remagen": (50.5741, 7.2290),
    "düsseldorf": (51.2277, 6.7735),
    "aachen": (50.7753, 6.0839),
    "leverkusen": (51.0459, 6.9844),
    "koblenz": (50.3569, 7.5890),
    "bornheim": (50.7577, 6.9987),
    "meckenheim": (50.6314, 7.0289),
    "rheinbach": (50.6255, 6.9499),
    "hennef": (50.7752, 7.2836),
    "lohmar": (50.8377, 7.2136),
    "much": (50.9025, 7.4021),
    "eitorf": (50.7696, 7.4524),
    "brühl": (50.8282, 6.9063),
    "poppelsdorf": (50.7267, 7.0863),
    "endenich": (50.7272, 7.0650),
    "beuel": (50.7390, 7.1170),
    "bad godesberg": (50.6830, 7.1500),
    "ippendorf": (50.7065, 7.0780),
    "dransdorf": (50.7355, 7.0508),
    "oberkassel": (50.7158, 7.1667),
    "oberdollendorf": (50.6990, 7.1850),
    "königswinter": (50.6741, 7.1844),
    "koenigswinter": (50.6741, 7.1844),
    "siebengebirge": (50.6710, 7.2370),
    "kottenforst": (50.6670, 7.0400),
    "venusberg": (50.7047, 7.0968),
    "bad neuenahr": (50.5439, 7.1113),
    "bad neuenahr-ahrweiler": (50.5439, 7.1113),
    "ahrweiler": (50.5415, 7.0947),
    "ahrtal": (50.5420, 7.0950),
    "dernau": (50.5332, 7.0447),
    "mayschoss": (50.5238, 7.0186),
    "altenahr": (50.5161, 6.9922),
    "walporzheim": (50.5361, 7.0751),
    "sinzig": (50.5439, 7.2460),
    "andernach": (50.4406, 7.4019),
    "namedy": (50.4564, 7.3665),
    "linz": (50.5686, 7.2849),
    "linz am rhein": (50.5686, 7.2849),
    "unkel": (50.6003, 7.2162),
    "bad hönningen": (50.5169, 7.3122),
    "bad hoenningen": (50.5169, 7.3122),
    "rolandseck": (50.6324, 7.2079),
    "drachenfels": (50.6652, 7.2107),
    "petersberg": (50.6869, 7.2078),
    "margarethenhöhe": (50.6840, 7.2430),
    "margarethenhoehe": (50.6840, 7.2430),
    "heisterbach": (50.6966, 7.2093),
    "lohrberg": (50.6837, 7.2545),
}

# ── Helpers ─────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def distance_score(km: float) -> float:
    """Score 0-1 based on distance. 0km=1.0, 25km=0.8, 50km=0.5, 75km=0.3"""
    if km <= 0:
        return 1.0
    return max(0.1, 1.0 - (km / MAX_RADIUS_KM) * 0.9)


def category_score(text: str) -> float:
    """Score based on category preference matching. Negative keywords override positives."""
    text_lower = text.lower()
    # Check for dealbreaker keywords first — these cap the score unless the event
    # is primarily an adult/outdoor/food experience that merely has a family side-offer
    # (e.g. AhrWeinWalk includes a Sunday kids quiz but is still a strong adult outing).
    negative_keywords = {"kinder", "kids", "grundschüler", "grundschueler", "familie",
                         "family", "vorlesen", "basteln", "jugendliche", "babys",
                         "spielgruppe", "krabbelgruppe", "eltern-kind"}
    adult_outdoor_signals = {
        "wein", "wine", "winzer", "weingut", "afterwalk", "genuss",
        "lounge", "beats", "festival", "markt", "flohmarkt", "street food", "kulinar",
        "stadtteilfest", "straßenfest", "strassenfest", "dorffest", "kirmes", "viertel", "meile",
    }
    has_negative = any(neg in text_lower for neg in negative_keywords)
    has_adult_outdoor_signal = any(sig in text_lower for sig in adult_outdoor_signals)
    if has_negative and not has_adult_outdoor_signal:
        return 0.25
    best = 0.8  # default
    for keyword, weight in CATEGORY_WEIGHT.items():
        if keyword in text_lower:
            best = max(best, weight)
    return best


def is_junk_event(ev: dict) -> bool:
    """Suppress legal pages, stale entries, classes, and generic low-signal sludge."""
    title = (ev.get("title") or "").lower()
    desc = (ev.get("description") or "").lower()
    venue = (ev.get("venue") or "").lower()
    link = (ev.get("link") or "").lower()
    text = f"{title} {desc} {venue} {link}"

    # Stale entries with parseable dates do not belong in a weekend report.
    dt = parse_date(ev.get("date") or "")
    if dt and not (TODAY <= dt <= END_DATE):
        return True

    junk_title_bits = {
        "privacy policy", "faq", "frequently asked questions", "contact", "kontakt",
        "imprint", "impressum", "corruption prevention", "accessibility statement",
        "newsletter", "jobs", "sitemap", "terms of use", "datenschutz",
        "veranstaltungen aktuell", "alle festivals und künstler auf einen blick",
        "auf einen blick", "10 best", "the best events", "alle veranstaltungen",
        "veranstaltungskalender", "event calendar", "sicherung der clubkultur",
        "abwechslungsreiches veranstaltungsjahr", "alle wein-events", "wein-events im ahrtal",
        "events 2026 in bonn", "veranstaltungen 2026 in bonn"
    }
    if any(bit in title for bit in junk_title_bits):
        return True

    junk_link_bits = {
        "/privacy", "/faq", "/contact", "/imprint", "/jobs", "/search", "/sitemap",
        "eventim.de/city", "livegigs.de", "news.de/lokales", "/metro-areas/"
    }
    if any(bit in link for bit in junk_link_bits):
        return True

    generic_low_value_bits = {
        "fortgeschrittene", "sprachkurs", "italienisch", "französisch", "englischkurs",
        "yogakurs", "offene sprechstunde", "beratung", "frauen in bewegung"
    }
    if any(bit in text for bit in generic_low_value_bits):
        return True

    if ev.get("source") in {"Exa Search", "Grok Search"}:
        # Search is useful for tiny local events, but it also returns static venue/shop/route pages.
        # Require both topical signal and some event/date signal unless it is a very explicit local event title.
        strong_signal = any(k in text for k in [
            "konzert", "concert", "ausstellung", "museum", "festival", "party", "dj",
            "techno", "electronic", "führung", "tour", "theater", "comedy", "lesung",
            "wein", "winzer", "weingut", "wanderung", "wandern", "wander", "walk", "ahrtal", "ahrweiler",
            "stadtteilfest", "straßenfest", "strassenfest", "dorffest", "kirmes",
            "poppelsdorf", "endenich", "beuel", "bad godesberg", "siebengebirge",
            "königswinter", "koenigswinter", "drachenfels", "petersberg", "heisterbach",
            "andernach", "namedy", "linz", "unkel", "remagen", "rolandseck", "bad honnef",
            "dernau", "mayschoss", "altenahr", "walporzheim", "weinprobe", "weinfest",
            "kottenforst", "natur", "rundgang", "genussmeile", "weinmeile"
        ])
        explicit_local_event = any(k in text for k in [
            "weinmeile", "genussmeile", "stadtteilfest", "straßenfest", "strassenfest",
            "dorffest", "kirmes", "weinfest", "wirtefestival", "promenadenfest",
        ])
        date_signal = bool(re.search(
            r"\b(20\d{2}|\d{1,2}\.\d{1,2}\.|\d{1,2}\s*(?:jan|feb|mär|mae|apr|mai|jun|jul|aug|sep|okt|nov|dez)|"
            r"montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|wochenende|heute|morgen|am\s+\d)",
            text,
            re.IGNORECASE,
        ))
        static_page_bits = [
            "öffnungszeiten", "route planen", "unser sortiment", "wanderwege in der nähe",
            "die besten", "wiki", "öffnungszeiten für", "website", "hotels", "immobilien",
        ]
        if any(bit in text for bit in static_page_bits) and not explicit_local_event:
            return True
        if not strong_signal or (not date_signal and not explicit_local_event):
            return True

    if ev.get("source") == "Bundeskunsthalle":
        allowed = {"Peter Hujar: Eyes Open in the Dark", "Amazônia: Indigene Welten", "Expedition to the World's Oceans"}
        if ev.get("title") not in allowed:
            return True

    return False

def guess_city_from_text(text: str) -> Optional[str]:
    """Try to extract city/location name from event text, preferring venue tokens over publisher branding."""
    text_lower = re.sub(r"bundesstadt\s+bonn", " ", (text or "").lower())
    # Longer/more-specific locations first; Bonn last so "... Siebengebirge | Bundesstadt Bonn" is not scored as 0km.
    cities = sorted(VENUE_COORDS, key=lambda c: (c == "bonn", -len(c)))
    for city in cities:
        if re.search(rf"(?<![a-zäöüß]){re.escape(city)}(?![a-zäöüß])", text_lower):
            return city
    return None


def coords_for_city(city: str) -> tuple:
    """Get coordinates for a city name."""
    return VENUE_COORDS.get(city.lower(), (BONN_LAT, BONN_LON))


def fetch_url(url: str, timeout: int = 15, headers: Optional[dict] = None) -> str:
    """Fetch URL with error handling."""
    hdrs = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.read().decode("utf-8", "ignore")


def post_json(url: str, payload: dict, timeout: int = 45, headers: Optional[dict] = None) -> dict:
    """POST JSON and parse response."""
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if headers:
        hdrs.update(headers)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode("utf-8", "ignore"))


def extract_json_array(text: str) -> list:
    """Best-effort parse of a JSON array from LLM/search output."""
    if not text:
        return []
    candidates = [text]
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S | re.I):
        candidates.append(m.group(1))
    arr_match = re.search(r"\[[\s\S]*\]", text)
    if arr_match:
        candidates.append(arr_match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
            return parsed if isinstance(parsed, list) else []
        except Exception:
            continue
    return []


def search_queries() -> list[str]:
    """Shared local/province/outdoor fallback queries."""
    month = TODAY.strftime('%B %Y')
    year = TODAY.strftime('%Y')
    return [
        f"Veranstaltungen Bonn Wochenende {month} Stadtteilfest Dorffest Markt Konzert Ausstellung",
        f"Bonn Poppelsdorf Endenich Beuel Bad Godesberg Kessenich Dottendorf Fest Meile Markt {month}",
        f"Bonn Nordstadt Südstadt Altstadt Tannenbusch Auerberg Röttgen Stadtteil Veranstaltung {month}",
        f"Bonn Konzert Club Party Live-Musik Indie Electronic Kulturzentrum {month}",
        f"Bonn Brotfabrik Pantheon Harmonie Bla Theater Lesung Comedy Kabarett Programm {month}",
        f"Bonn Museum Ausstellung Vernissage Kunstmuseum Bundeskunsthalle LVR Haus der Geschichte {month}",
        f"Bonn Flohmarkt Trödelmarkt Wochenmarkt Bauernmarkt Kunsthandwerkermarkt {month}",
        f"Königswinter Siebengebirge Drachenfels Wanderung Führung Markt Wochenende {month}",
        f"site:vv-siebengebirge.de/veranstaltung Siebengebirge Wanderung Natur Führung {year}",
        f"Ahrtal Ahrweiler Dernau Mayschoss Weinwanderung Weinprobe Weinfest {month}",
        f"site:ahrtal.com/de/events Ahrtal Event Wein Wanderung Führung {year}",
        f"Andernach Bad Honnef Linz Unkel Remagen Open-Air Schlossgarten Markt Wochenende {month}",
        f"Rhein-Sieg-Kreis Siegburg Troisdorf Sankt Augustin Hennef Veranstaltung Fest {month}",
        f"Bonn Umgebung Natur Wanderung Führung Siebengebirge Kottenforst Wochenende {month}",
    ]


def search_result_event(title: str, link: str, desc: str, source: str, trust: float) -> Optional[dict]:
    """Convert a search result/candidate into a low-trust event."""
    full_text = f"{title} {desc} {link}"
    extracted_dates = extract_dates(full_text)
    if not date_range_overlaps(extracted_dates):
        return None
    city_guess = guess_city_from_text(full_text) or "Bonn area"
    city_coords = coords_for_city(city_guess)
    km = haversine(BONN_LAT, BONN_LON, *city_coords)
    if km > MAX_RADIUS_KM:
        return None
    candidate = {
        "title": unescape(clean_html(title))[:140],
        "date": extracted_dates[0].strftime("%Y-%m-%d") if extracted_dates else "",
        "time": "",
        "venue": "",
        "city": city_guess.title(),
        "description": clean_html(desc)[:260],
        "price": "",
        "link": link,
        "distance_km": round(km, 1),
        "score": round(distance_score(km) * category_score(full_text) * trust, 2),
        "source": source,
        "category": "search fallback",
    }
    return None if is_junk_event(candidate) else candidate


def clean_html(text: str) -> str:
    """Strip tags/entities and normalize whitespace for scraped descriptions."""
    text = unescape(text or "")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_iso_date(text: str) -> Optional[datetime]:
    """Parse ISO-ish datetimes from JSON-LD, preserving only local date/time."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def make_event(title: str, start_dt: Optional[datetime], end_dt: Optional[datetime], venue: str,
               city: str, description: str, link: str, source: str, category: str,
               trust: float = 1.0, time_text: str = "") -> Optional[dict]:
    """Build a scored event dict and apply range/radius checks."""
    if not title:
        return None
    if start_dt and end_dt and (end_dt < TODAY or start_dt > END_DATE):
        return None
    if start_dt and not end_dt and not (TODAY <= start_dt <= END_DATE):
        return None
    coords = coords_for_city(city)
    km = haversine(BONN_LAT, BONN_LON, *coords)
    if km > MAX_RADIUS_KM:
        return None
    if start_dt and end_dt and start_dt.date() != end_dt.date():
        date_text = f"{start_dt.strftime('%Y-%m-%d')}–{end_dt.strftime('%Y-%m-%d')}"
    elif start_dt:
        date_text = start_dt.strftime("%Y-%m-%d")
    else:
        date_text = ""
    if not time_text and start_dt and (start_dt.hour or start_dt.minute):
        time_text = start_dt.strftime("%H:%M")
        if end_dt and (end_dt.hour or end_dt.minute):
            time_text += "–" + end_dt.strftime("%H:%M")
    full_text = f"{title} {venue} {city} {description} {category}"
    ev = {
        "title": clean_html(title)[:140],
        "date": date_text,
        "time": time_text,
        "venue": clean_html(venue)[:120],
        "city": clean_html(city).title(),
        "description": clean_html(description)[:260],
        "price": "",
        "link": link,
        "distance_km": round(km, 1),
        "score": round(distance_score(km) * category_score(full_text) * trust, 2),
        "source": source,
        "category": category,
    }
    return None if is_junk_event(ev) else ev


def jsonld_event_items(html: str) -> list:
    """Extract schema.org Event objects from JSON-LD blobs."""
    items = []

    def walk(obj):
        if isinstance(obj, list):
            for x in obj:
                walk(x)
        elif isinstance(obj, dict):
            typ = obj.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if "Event" in types:
                items.append(obj)
            for key in ("@graph", "itemListElement"):
                if key in obj:
                    walk(obj[key])

    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        raw = m.group(1).strip()
        try:
            walk(json.loads(raw))
        except Exception:
            continue
    return items


def events_from_jsonld(html: str, source: str, default_city: str, category: str, trust: float, default_link: str) -> list:
    events = []
    for item in jsonld_event_items(html):
        title = item.get("name", "")
        start_dt = parse_iso_date(item.get("startDate", ""))
        end_dt = parse_iso_date(item.get("endDate", "")) or start_dt
        loc = item.get("location") or {}
        venue = loc.get("name", "") if isinstance(loc, dict) else ""
        address = loc.get("address", {}) if isinstance(loc, dict) else {}
        city = default_city
        if isinstance(address, dict):
            city = address.get("addressLocality") or city
        city = re.sub(r"^\d{5}\s+", "", str(city)).strip()
        desc = item.get("description", "")
        link = item.get("url") or default_link
        ev = make_event(title, start_dt, end_dt, venue, city, desc, link, source, category, trust)
        if ev:
            events.append(ev)
    return events


def parse_date(text: str) -> Optional[datetime]:
    """Try various date formats, including ranges and German month names."""
    text = (text or "").strip()
    if not text:
        return None
    # For ranges, parse the first date; range-overlap checks should parse both explicitly where needed.
    text = re.split(r"\s*(?:–|\bbis\b)\s*", text, maxsplit=1)[0].strip()
    for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%a, %d %b %Y %H:%M:%S %z"]:
        try:
            return datetime.strptime(text[:len(fmt)+5], fmt).replace(tzinfo=None)
        except (ValueError, IndexError):
            continue
    month_map = {
        "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3, "mae": 3,
        "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
        "august": 8, "aug": 8, "september": 9, "sep": 9, "oktober": 10, "okt": 10,
        "november": 11, "nov": 11, "dezember": 12, "dez": 12,
    }
    m = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(20\d{2})", text)
    if m:
        day, mon, year = m.groups()
        mon_num = month_map.get(mon.lower())
        if mon_num:
            return datetime(int(year), mon_num, int(day))
    # Try ISO format
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        pass
    return None


def extract_dates(text: str) -> list[datetime]:
    """Extract parseable dates from free text for search-result filtering."""
    text = text or ""
    dates = []
    patterns = [
        r"20\d{2}-\d{2}-\d{2}",
        r"\d{1,2}\.\d{1,2}\.20\d{2}",
        r"\d{1,2}\.\d{1,2}\.\d{2}\b",
        r"\d{1,2}\.\s*(?:Januar|Jan|Februar|Feb|März|Maerz|Mär|Mae|April|Apr|Mai|Juni|Jun|Juli|Jul|August|Aug|September|Sep|Oktober|Okt|November|Nov|Dezember|Dez)\s*20\d{2}",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            dt = parse_date(m.group(0))
            if dt:
                dates.append(dt)
    return dates


def date_range_overlaps(dates: list[datetime]) -> bool:
    """True if any extracted date is inside the report window; no dates means unknown."""
    if not dates:
        return True
    return any(TODAY <= dt <= END_DATE for dt in dates)


def in_date_range(date_str: str) -> bool:
    """Check if a date string falls within our range."""
    dt = parse_date(date_str)
    if dt is None:
        return True  # include if we can't parse (might be relevant)
    return TODAY <= dt <= END_DATE


# ── Source Fetchers ─────────────────────────────────────────────────

def fetch_koeln_api() -> list:
    """Köln Open Data Events API — structured JSON."""
    source = "Köln Open Data"
    try:
        url = f"http://www.stadt-koeln.de/externe-dienste/open-data/events-od.php?out=json&ndays={DAYS_AHEAD}"
        raw = fetch_url(url, timeout=20)
        data = json.loads(raw)
        events = []
        for item in data.get("items", []):
            title = item.get("title", "").strip()
            if not title:
                continue

            begin = item.get("beginndatum", "")
            end = item.get("endedatum", "")
            venue = item.get("veranstaltungsort", "")
            desc = item.get("description", "")
            time_str = item.get("uhrzeit", "")
            price = item.get("preis", "")
            lat = float(item.get("latitude", 0) or 0)
            lon = float(item.get("longitude", 0) or 0)
            link = item.get("link", "")
            district = item.get("stadtteil", "")

            # Date filter: check if event overlaps with our date range
            begin_dt = parse_date(begin) if begin else None
            end_dt = parse_date(end) if end else begin_dt
            if begin_dt and end_dt:
                if end_dt < TODAY or begin_dt > END_DATE:
                    continue
            elif begin_dt:
                if begin_dt > END_DATE:
                    continue

            # Distance calc
            if lat and lon:
                km = haversine(BONN_LAT, BONN_LON, lat, lon)
            else:
                km = haversine(BONN_LAT, BONN_LON, *coords_for_city("köln"))

            if km > MAX_RADIUS_KM:
                continue

            # Clean price HTML
            if price:
                price = re.sub(r"<[^>]+>", " ", price).strip()
                price = re.sub(r"\s+", " ", price)

            full_text = f"{title} {desc} {venue}"
            score = distance_score(km) * category_score(full_text)

            events.append({
                "title": unescape(title),
                "date": begin,
                "time": unescape(re.sub(r"<[^>]+>", "", time_str).strip()) if time_str else "",
                "venue": unescape(venue),
                "city": "Köln" + (f" ({district})" if district else ""),
                "description": unescape(desc),
                "price": unescape(price) if price else "",
                "link": link,
                "distance_km": round(km, 1),
                "score": round(score, 2),
                "source": source,
                "category": "",
            })
        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []


def fetch_bonn_html() -> list:
    """Bonn.de Veranstaltungskalender — HTML scraping."""
    source = "Bonn.de"
    try:
        url = "https://www.bonn.de/bonn-erleben/ausgehen-und-erleben/veranstaltungskalender.php"
        html = fetch_url(url)
        events = []

        # Extract event links with category and date info
        pattern = r'<a[^>]*href="(/veranstaltungskalender/[^"]+?)"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, html, re.DOTALL)

        for href, text in matches:
            clean = re.sub(r"<[^>]+>", " ", text).strip()
            clean = re.sub(r"\s+", " ", clean)
            clean = unescape(clean)

            if "speichern" in clean.lower() or len(clean) < 10:
                continue

            # Parse: "Category DD.MM.YYYY HH:MM Uhr, ... Title Description"
            cat_match = re.match(r"^([\w/|]+(?:\s*\|\s*[\w/]+)*)\s*", clean)
            category = cat_match.group(1) if cat_match else ""

            # Extract dates
            dates = re.findall(r"(\d{2}\.\d{2}\.\d{4})", clean)

            # Extract title: strip category prefix, all dates, times, ellipsis
            title_part = clean
            # Remove category prefix
            if category:
                title_part = title_part[len(category):].strip()
            # Remove all date/time patterns
            title_part = re.sub(r"\d{2}\.\d{2}\.\d{4}\s*\d{0,2}:?\d{0,2}\s*(?:Uhr)?\s*,?\s*", "", title_part)
            # Remove nbsp and ellipsis
            title_part = re.sub(r"[\xa0\u00a0]", " ", title_part)
            title_part = re.sub(r"^[\s.,;]*\.{3}\s*", "", title_part)
            title_part = re.sub(r"^\.\.\.\s*", "", title_part)
            title_part = title_part.strip()

            # Truncate at common description starters (title bleeds into description)
            for sep in [" Bei der ", " Die ", " Spannende ", " Im Rahmen ", " Informieren ",
                        " Auf dieser ", " Eine ", " Das ", " Monatlicher "]:
                if sep in title_part and len(title_part.split(sep)[0]) > 10:
                    title_part = title_part.split(sep)[0]
                    break
            # Cap at reasonable title length
            if len(title_part) > 80:
                # Try to find a natural break
                for brk in [" - ", " – ", " | ", ". "]:
                    if brk in title_part[:80]:
                        break
                else:
                    title_part = title_part[:80]

            if not title_part or len(title_part) < 3:
                continue

            # Check if any date is in range
            if dates:
                any_in_range = any(in_date_range(d) for d in dates)
                if not any_in_range:
                    continue

            full_text = f"{category} {title_part}"
            score = distance_score(0) * category_score(full_text)  # distance 0 = Bonn

            events.append({
                "title": title_part[:120],
                "date": dates[0] if dates else "",
                "time": "",
                "venue": "",
                "city": "Bonn",
                "description": "",
                "price": "",
                "link": f"https://www.bonn.de{href}",
                "distance_km": 0,
                "score": round(score, 2),
                "source": source,
                "category": category,
            })
        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []


def fetch_bonn_rss() -> list:
    """Bonn.de RSS feed — structured XML."""
    source = "Bonn.de RSS"
    try:
        url = "https://www.bonn.de/bonn-erleben/ausgehen-und-erleben/veranstaltungskalender.php?sp%3Aout=rss&sp%3Acmp=search-1-0-searchResult&action=submit"
        xml_data = fetch_url(url)
        root = ET.fromstring(xml_data)
        events = []

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()

            if not title:
                continue

            if pub_date and not in_date_range(pub_date):
                continue

            full_text = f"{title} {desc}"
            score = distance_score(0) * category_score(full_text)

            events.append({
                "title": unescape(title),
                "date": pub_date[:16] if pub_date else "",
                "time": "",
                "venue": "",
                "city": "Bonn",
                "description": unescape(re.sub(r"<[^>]+>", "", desc))[:200] if desc else "",
                "price": "",
                "link": link,
                "distance_km": 0,
                "score": round(score, 2),
                "source": source,
                "category": "",
            })
        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []


# ── Generic iCal (RFC 5545) ─────────────────────────────────────────
# Many Bonn/NRW venues run WordPress + "The Events Calendar" (Tribe), which
# exposes a clean .ics feed at ?post_type=tribe_events&ical=1. iCal is far more
# reliable than scraping their HTML. Reuse fetch_ical() for any such source.

def _ical_unfold(text: str) -> str:
    """RFC 5545 line unfolding: a CRLF followed by space/tab continues the prior line."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _ical_unescape(text: str) -> str:
    return (text.replace("\\n", " ").replace("\\N", " ")
                .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")).strip()


def _ical_parse_dt(value: str) -> Optional[datetime]:
    v = (value or "").strip()
    if re.match(r"^\d{8}T\d{6}Z?$", v):
        return datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if re.match(r"^\d{8}$", v):
        return datetime.strptime(v, "%Y%m%d")
    return parse_iso_date(v)


def fetch_ical(url: str, source: str, default_city: str, category: str = "", trust: float = 1.0) -> list:
    """Generic RFC 5545 iCal/.ics fetcher (Tribe Events, webcal, Meetup group feeds)."""
    try:
        raw = _ical_unfold(fetch_url(url, timeout=20))
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S):
        props = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            name = key.split(";")[0].strip().upper()
            if name in ("SUMMARY", "DTSTART", "DTEND", "DESCRIPTION", "LOCATION", "URL", "CATEGORIES"):
                props.setdefault(name, val)
        if not props.get("SUMMARY"):
            continue
        start_dt = _ical_parse_dt(props.get("DTSTART", ""))
        end_dt = _ical_parse_dt(props.get("DTEND", "")) or start_dt
        cat = category or _ical_unescape(props.get("CATEGORIES", ""))
        ev = make_event(
            _ical_unescape(props["SUMMARY"]),
            start_dt, end_dt,
            _ical_unescape(props.get("LOCATION", "")),
            default_city,
            _ical_unescape(props.get("DESCRIPTION", "")),
            (props.get("URL", "") or url).strip(),
            source, cat, trust,
        )
        if ev:
            events.append(ev)
    return events


def fetch_harmonie_bonn() -> list:
    """Harmonie Bonn — concerts + club nights via Tribe Events iCal feed."""
    return fetch_ical(
        "https://www.harmonie-bonn.de/?post_type=tribe_events&ical=1",
        "Harmonie Bonn", "Bonn", "concert", 1.0,
    )


# Curated Bonn-area Meetup groups. Each exposes a public iCal feed at
# https://www.meetup.com/<slug>/events/ical/ (no auth). Re-probe periodically and
# drop dead slugs (404 = wrong slug; 200 with 0 VEVENT = inactive group).
# (slug, default_city, category-hint, trust)
MEETUP_GROUPS = [
    ("bonner-ki-meetup", "Bonn", "ki tech meetup", 0.95),
    ("jug-bonn", "Bonn", "java tech meetup", 0.9),
    ("azure-bonn-meetup", "Bonn", "cloud tech meetup", 0.9),
    ("rudel-koeln", "Köln", "wanderung outdoor natur meetup", 0.85),
    ("board-games-in-bonn", "Bonn", "spiele meetup", 0.8),
    ("sprachcafe-bonn", "Bonn", "sprache meetup", 0.8),
]


def fetch_meetup_groups() -> list:
    """Curated Bonn-area Meetup groups via their public per-group iCal feeds."""
    events = []
    for slug, city, category, trust in MEETUP_GROUPS:
        events.extend(fetch_ical(
            f"https://www.meetup.com/{slug}/events/ical/",
            "Meetup", city, category, trust,
        ))
    return events


def fetch_bonn_jetzt() -> list:
    """Bonn.jetzt homepage — SSR event cards for Bonn's digital/community scene."""
    source = "Bonn.jetzt"
    try:
        html = fetch_url("https://bonn.jetzt/")
        events = []

        article_pattern = re.compile(r'<article[^>]*itemtype="https://schema.org/Event".*?</article>', re.DOTALL)
        articles = article_pattern.findall(html)

        for article in articles:
            title_match = re.search(r'<h2[^>]*class="title p-name"[^>]*>(.*?)</h2>', article, re.DOTALL)
            link_match = re.search(r'<a href="(/event/[^"]+)"[^>]*itemprop="url">', article)
            start_match = re.search(r'<time[^>]*datetime="([^"]+)"[^>]*itemprop="startDate"[^>]*>(.*?)</time>', article, re.DOTALL)
            end_match = re.search(r'<time[^>]*itemprop="endDate"[^>]*content="([^"]+)"', article)
            venue_match = re.search(r'<span itemprop="name">(.*?)</span>', article, re.DOTALL)
            addr_match = re.search(r'<div itemprop="address"[^>]*>(.*?)</div>', article, re.DOTALL)
            tags = re.findall(r'<span class="v-chip__content">(.*?)</span>', article, re.DOTALL)

            title = unescape(re.sub(r'<[^>]+>', '', title_match.group(1)).strip()) if title_match else ''
            if not title:
                continue

            start_raw = start_match.group(1).strip() if start_match else ''
            start_text = unescape(re.sub(r'<[^>]+>', ' ', start_match.group(2)).strip()) if start_match else ''
            end_raw = end_match.group(1).strip() if end_match else ''
            if start_raw and not in_date_range(start_raw):
                if not (end_raw and in_date_range(end_raw)):
                    continue

            venue = unescape(re.sub(r'<[^>]+>', '', venue_match.group(1)).strip()) if venue_match else ''
            address = unescape(re.sub(r'<[^>]+>', '', addr_match.group(1)).strip()) if addr_match else ''
            city = guess_city_from_text(address or venue or title) or 'bonn'
            lat, lon = coords_for_city(city)
            km = haversine(BONN_LAT, BONN_LON, lat, lon)
            if km > MAX_RADIUS_KM:
                continue

            tag_text = ' '.join(unescape(t).strip() for t in tags)
            full_text = f"{title} {venue} {address} {tag_text}"
            score = distance_score(km) * category_score(full_text)

            events.append({
                "title": title,
                "date": start_raw,
                "time": start_text,
                "venue": venue,
                "city": city.title() if city else "Bonn",
                "description": tag_text,
                "price": "",
                "link": f"https://bonn.jetzt{link_match.group(1)}" if link_match else "https://bonn.jetzt/",
                "distance_km": round(km, 1),
                "score": round(score, 2),
                "source": source,
                "category": ", ".join(tags[:3]),
            })
        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []


def fetch_rheinaue_flohmarkt() -> list:
    """Recurring Bonn Rheinauen-Flohmarkt: third Saturday from April to October."""
    source = "Rheinauen-Flohmarkt"
    events = []

    def third_saturday(year: int, month: int) -> datetime:
        first = datetime(year, month, 1)
        days_until_sat = (5 - first.weekday()) % 7
        return first + timedelta(days=days_until_sat + 14)

    for year in {TODAY.year, END_DATE.year}:
        for month in range(4, 11):
            dt = third_saturday(year, month)
            if TODAY <= dt <= END_DATE:
                events.append({
                    "title": "Rheinauen-Flohmarkt",
                    "date": dt.strftime("%Y-%m-%d"),
                    "time": "08:00–18:00",
                    "venue": "Freizeitpark Rheinaue",
                    "city": "Bonn",
                    "description": "Großer Bonner Flohmarkt in der Rheinaue. Nur Gebrauchtwaren und Kunsthandwerk.",
                    "price": "",
                    "link": "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/flohmarkt-rheinaue.php",
                    "distance_km": 3.5,
                    "score": round(distance_score(3.5) * 1.15, 2),
                    "source": source,
                    "category": "markt flohmarkt outdoor",
                })
    return events


def fetch_bonn_local_recurring() -> list:
    """Curated Bonn district/province events from official yearly lists and stable local patterns."""
    source = "Bonn local recurring"
    events = []

    def add(title: str, start: str, end: str, time: str, venue: str, city: str,
            description: str, link: str, category: str = "stadtteilfest market outdoor"):
        start_dt = parse_date(start)
        end_dt = parse_date(end or start) or start_dt
        if start_dt and end_dt and (end_dt < TODAY or start_dt > END_DATE):
            return
        km = haversine(BONN_LAT, BONN_LON, *coords_for_city(city))
        if km > MAX_RADIUS_KM:
            return
        date_text = start if start == end or not end else f"{start}–{end}"
        full_text = f"{title} {venue} {city} {description} {category} local neighborhood nature province"
        events.append({
            "title": title,
            "date": date_text,
            "time": time,
            "venue": venue,
            "city": city.title() if city.lower() not in {"bad godesberg"} else "Bad Godesberg",
            "description": description,
            "price": "",
            "link": link,
            "distance_km": round(km, 1),
            "score": round(distance_score(km) * category_score(full_text), 2),
            "source": source,
            "category": category,
        })

    # Official Bonn 2026 yearly-event press list: local markets, district festivals,
    # village fairs, and neighbourhood events that often do not surface in API feeds.
    yearly_link = "https://www.bonn.de/pressemitteilungen/dezember/abwechslungsreiches-veranstaltungsjahr-2026-in-bonn.php"
    add("Beueler Frühlingsfest", "2026-03-27", "2026-03-29", "", "Rheinufer Beuel / Mirecourtplatz", "Beuel",
        "Lokales Frühlingsfest am Beueler Rheinufer.", yearly_link, "stadtteilfest outdoor")
    add("Osterkirmes Beuel", "2026-03-30", "2026-04-12", "", "Rheinufer Beuel", "Beuel",
        "Kirmes am Beueler Rheinufer.", yearly_link, "kirmes outdoor")
    add("Frühlingsmarkt Bonn", "2026-05-09", "2026-05-09", "", "Münsterplatz", "Bonn",
        "Innenstadtmarkt auf dem Münsterplatz.", yearly_link, "market outdoor")
    add("GoVinum - Bad Godesberger Weinfest", "2026-05-08", "2026-05-10", "", "Theaterplatz", "Bad Godesberg",
        "Wein, Musik und Kulinarik zwischen Godesburg und Redoute.", "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/GoVinum-Bad-Godesberger-Weinfest.php", "weinfest food outdoor")
    add("Beueler Wirtefestival", "2026-06-04", "2026-06-05", "", "Rheinufer Beuel", "Beuel",
        "Lokales Wirtefestival am Rhein zwischen China-Schiff und Bahnhöfchen.", yearly_link, "food festival outdoor")
    add("Kirmes Ippendorf", "2026-06-12", "2026-06-14", "", "Bernhard-Berzheim-Platz", "Ippendorf",
        "Stadtteilkirmes in Ippendorf.", yearly_link, "kirmes stadtteilfest")
    add("Töpfermarkt Bonn", "2026-06-13", "2026-06-14", "", "Münsterplatz", "Bonn",
        "Keramik- und Handwerksmarkt in der Innenstadt.", yearly_link, "market kunsthandwerk outdoor")
    add("Antik-, Kunst- & Designmarkt Bonn", "2026-06-21", "2026-06-21", "", "Münsterplatz", "Bonn",
        "Antik-, Kunst- und Designmarkt in Bonn.", yearly_link, "market art design outdoor")
    add("Ippendorfer Kirmes", "2026-07-03", "2026-07-05", "", "Bernhard-Berzheim-Platz", "Ippendorf",
        "Stadtteilkirmes in Ippendorf.", yearly_link, "kirmes stadtteilfest")
    add("Kirmes und Dorffest Endenich", "2026-07-17", "2026-07-19", "", "Magdalenenplatz", "Endenich",
        "Dorffest und Kirmes im Bonner Stadtteil Endenich.", yearly_link, "dorffest kirmes stadtteilfest")
    add("Fest der Beueler Vereine / Promenadenfest", "2026-08-29", "2026-08-30", "", "Rheinufer Beuel", "Beuel",
        "Promenadenfest lokaler Beueler Vereine am Rhein.", yearly_link, "stadtteilfest outdoor")
    add("Dorffest der Dransdorfer Vereine", "2026-09-05", "2026-09-06", "", "Park am Kettelerplatz", "Dransdorf",
        "Dorffest lokaler Vereine in Dransdorf.", yearly_link, "dorffest stadtteilfest outdoor")
    add("Beuel-Fest", "2026-09-05", "2026-09-06", "", "Beuel-Zentrum / Rheinufer", "Beuel",
        "Stadtteilfest im Beueler Zentrum und am Rheinufer.", yearly_link, "stadtteilfest outdoor")
    add("Pützchens Markt", "2026-09-11", "2026-09-15", "", "Marktwiesen Pützchen", "Beuel",
        "Großer Bonner Jahrmarkt in Pützchen.", yearly_link, "kirmes market outdoor")
    add("Poppelsdorfer Straßenfest", "2026-09-19", "2026-09-19", "11:00–24:00", "Clemens-August-Straße", "Poppelsdorf",
        "Stadtteilfest auf der Poppelsdorfer Meile mit Gastronomie, lokalen Geschäften, Vereinen und Musik.", yearly_link, "stadtteilfest food music outdoor")
    add("Street Food Festival Beuel", "2026-09-18", "2026-09-20", "", "Rheinufer Beuel", "Beuel",
        "Street-Food-Festival am Beueler Rheinufer.", yearly_link, "food festival outdoor")
    add("Bonn-Fest", "2026-09-25", "2026-09-27", "", "Innenstadt Bonn", "Bonn",
        "Großes Innenstadtfest auf Markt, Münsterplatz, Friedensplatz und umliegenden Straßen.", yearly_link, "stadtfest food music outdoor")
    add("Nikolausmarkt Beuel", "2026-11-27", "2026-11-29", "", "Hermannstraße / St. Josef", "Beuel",
        "Lokaler Nikolausmarkt in Beuel.", yearly_link, "market winter outdoor")
    add("Weihnachtsmarkt Holzlarer Mühle", "2026-12-05", "2026-12-05", "", "Holzlarer Mühle", "Beuel",
        "Kleiner lokaler Weihnachtsmarkt an der Holzlarer Mühle.", yearly_link, "market winter local")

    # Known local Weinmeile pattern. It is not reliably published as a normal event page;
    # social/community listings showed the 2025 edition on the Friday after Himmelfahrt week.
    # Keep this as a low-ish trust candidate only when the exact 2026 analogue falls in range.
    poppelsdorf_weinmeile = datetime(2026, 5, 15)
    if TODAY <= poppelsdorf_weinmeile <= END_DATE:
        add("Poppelsdorfer Weinmeile / Wein Meile", "2026-05-15", "2026-05-15", "abends", "Clemens-August-Straße", "Poppelsdorf",
            "Kleine Genuss-/Weinmeile auf der Poppelsdorfer Gastro-Meile; bislang eher über Community-Listings als offizielle Kalender auffindbar, daher vor Besuch gegenprüfen.",
            "https://community.gemeinsamerleben.com/community/friendseek/appointments/iEo8RvuFFqn", "weinmeile food music local")

    return events


def fetch_ahrtal_highlights() -> list:
    """Ahrtal official event highlights near Bonn, especially wine/walk weekend events."""
    source = "Ahrtal"
    events = []
    targets = [
        "https://www.ahrtal.de/ahrweinwalk",
        "https://ticket.ahrtal.de/event/ahrweinwalk-8ceohk",
    ]
    month_map = {
        "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
        "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
        "oktober": 10, "november": 11, "dezember": 12,
    }

    def parse_german_range(text: str) -> tuple[Optional[datetime], Optional[datetime]]:
        # Examples: "Vom 14. Mai bis zum 17. Mai 2026", "14. bis 17. Mai 2026"
        patterns = [
            r"(?:vom\s*)?(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)?\s*bis\s*(?:zum\s*)?(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(20\d{2})",
            r"(\d{1,2})\.\s*bis\s*(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(20\d{2})",
        ]
        lowered = text.lower()
        for pat in patterns:
            m = re.search(pat, lowered, re.IGNORECASE)
            if not m:
                continue
            groups = m.groups()
            try:
                if len(groups) == 5:
                    d1, mon1, d2, mon2, year = groups
                    mon1 = mon1 or mon2
                else:
                    d1, d2, mon2, year = groups
                    mon1 = mon2
                start = datetime(int(year), month_map[mon1.lower()], int(d1))
                end = datetime(int(year), month_map[mon2.lower()], int(d2))
                return start, end
            except Exception:
                continue
        return None, None

    try:
        combined_html = "\n".join(fetch_url(url, timeout=20) for url in targets)
        text = unescape(re.sub(r"<[^>]+>", " ", combined_html))
        text = re.sub(r"\s+", " ", text)
        if "ahrweinwalk" not in text.lower():
            return []

        start_dt, end_dt = parse_german_range(text)
        if start_dt and end_dt and (end_dt < TODAY or start_dt > END_DATE):
            return []
        if not start_dt and "ahrweinwalk" not in text.lower():
            return []

        city = "Ahrweiler"
        km = haversine(BONN_LAT, BONN_LON, *coords_for_city(city))
        if km > MAX_RADIUS_KM:
            return []

        date_text = ""
        if start_dt and end_dt:
            date_text = f"{start_dt.strftime('%Y-%m-%d')}–{end_dt.strftime('%Y-%m-%d')}"

        desc = (
            "6 km Weinwanderung durch die Ahrweiler Weinberge mit Winzerständen, "
            "AfterWalkLounge, ÖPNV-Anreise über Bahnhof Ahrweiler Markt/RB30; "
            "Sonntag mit Kinderquiz."
        )
        full_text = f"AhrWeinWalk Ahrtal Ahrweiler Weinwanderung Winzer outdoor {desc}"
        events.append({
            "title": "AhrWeinWalk",
            "date": date_text,
            "time": "Winzerstände 11:00–18:00; Lounge je nach Tag bis 20–22 Uhr",
            "venue": "Ahrweiler Weinberge / Winzerkapelle St. Urban",
            "city": city,
            "description": desc,
            "price": "Starterpaket online meist günstiger; Ticketseite prüfen",
            "link": "https://www.ahrtal.de/ahrweinwalk",
            "distance_km": round(km, 1),
            "score": round(distance_score(km) * category_score(full_text), 2),
            "source": source,
            "category": "weinwanderung outdoor festival",
        })
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
    return events


def fetch_koenigswinter_calendar() -> list:
    """Königswinter official calendar: museums, guided tours, markets, hiking, small local events."""
    source = "Königswinter"
    url = "https://www.koenigswinter.de/de/veranstaltungen.html"
    try:
        html = fetch_url(url, timeout=25)
        events = []
        # GeoCMS cards: category span, h4 link, date div, location span.
        card_re = re.compile(
            r'<span class="text-muted">\s*(?P<cat>.*?)\s*</span>\s*'
            r'<h4>\s*<a href="(?P<link>[^"]+)">(?P<title>.*?)</a>\s*</h4>'
            r'(?:\s*<h6[^>]*>(?P<subtitle>.*?)</h6>)?'
            r'.*?<div class="mb-2">.*?</i>\s*(?P<date>\d{2}\.\d{2}\.20\d{2})(?:\s*-\s*(?P<end>\d{2}\.\d{2}\.20\d{2}))?(?:\s*von\s*(?P<time>.*?))?\s*</div>'
            r'.*?<span class="gcevent-list-location-span">\s*(?P<venue>.*?)\s*</span>',
            re.S | re.I,
        )
        for m in card_re.finditer(html):
            title = clean_html(m.group("title"))
            subtitle = clean_html(m.group("subtitle") or "")
            category = clean_html(m.group("cat")) + " königswinter siebengebirge outdoor wanderung markt führung"
            start_dt = parse_date(m.group("date"))
            end_dt = parse_date(m.group("end") or m.group("date"))
            link = urllib.parse.urljoin("https://www.koenigswinter.de/", m.group("link"))
            time_text = clean_html(m.group("time") or "")[:80]
            ev = make_event(title, start_dt, end_dt, clean_html(m.group("venue")), "Königswinter",
                            subtitle, link, source, category, 0.95, time_text)
            if ev:
                events.append(ev)
        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []


def fetch_vvs_siebengebirge() -> list:
    """Verschönerungsverein Siebengebirge: guided hikes, nature days, forest/outdoor events."""
    source = "VVS Siebengebirge"
    url = "https://www.vv-siebengebirge.de/veranstaltungen/"
    try:
        html = fetch_url(url, timeout=25)
        return events_from_jsonld(html, source, "Königswinter", "siebengebirge wanderung natur outdoor führung", 1.1, url)
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []


def fetch_siebengebirge_tourismus() -> list:
    """Tourismus Siebengebirge / museum current dates: Drachenfels walks, culinary tours, small culture."""
    source = "Tourismus Siebengebirge"
    url = "https://www.siebengebirge.com/index.php?catid=27&id=221%3Averanstaltungen-aktuell&view=article"
    try:
        html = fetch_url(url, timeout=20)
        text = clean_html(html)
        events = []
        month_map = {
            "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
            "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
            "november": 11, "dezember": 12,
        }
        pat = re.compile(
            r"(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),\s*"
            r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(20\d{2}),\s*"
            r"(?:(?:ab\s*ca\.\s*)?(\d{1,2})(?:[.:](\d{2}))?\s*Uhr:\s*)?"
            r"(.+?)(?=(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),\s*\d{1,2}\.\s*[A-Za-zäöüÄÖÜ]+\s*20\d{2}|Sonderausstellungen|Weitere Informationen|$)",
            re.S,
        )
        for m in pat.finditer(text):
            day, mon, year, hh, mm, title = m.groups()
            mon_num = month_map.get(mon.lower())
            if not mon_num:
                continue
            start_dt = datetime(int(year), mon_num, int(day), int(hh or 0), int(mm or 0))
            title = clean_html(title).strip(" .")
            title = re.split(r"\s+Am Sonntag,|\s+Als besondere Stadtführung", title)[0].strip(" .")
            ev = make_event(title, start_dt, start_dt, "Siebengebirgsmuseum / Königswinter", "Königswinter",
                            "Tourismus-Siebengebirge-Termin, oft Führung, Wanderung oder lokales Kulturformat.",
                            url, source, "siebengebirge drachenfels führung wanderung kultur outdoor", 0.95)
            if ev:
                events.append(ev)
        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []


def fetch_ahrtal_calendar() -> list:
    """Ahrtal/Ahrwein calendars: wine walks, vineyard hikes, tastings and smaller valley events."""
    source = "Ahrtal calendar"
    urls = [
        "https://www.ahrtal.com/de/events",
        "https://www.ahrwein.de/veranstaltungen/alle-wein-events-im-ahrtal",
    ]
    events = []
    try:
        for url in urls:
            html = fetch_url(url, timeout=25)
            raw_text = unescape(re.sub(r"<[^>]+>", "\n", html))
            lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
            lines = [line for line in lines if line]
            # TYPO3 list shape: title / description / Mehr / dd.mm.yyyy / title / description / Mehr / date.
            for i, line in enumerate(lines):
                if line != "Mehr" or i + 1 >= len(lines) or not re.fullmatch(r"\d{2}\.\d{2}\.20\d{2}", lines[i + 1]):
                    continue
                if i < 2:
                    continue
                date_s = lines[i + 1]
                title = clean_html(lines[i - 2])
                desc = clean_html(lines[i - 1])
                combined = f"{title} {desc}".lower()
                if not any(k in combined for k in ["wein", "wander", "führung", "tour", "fest", "markt", "genuss", "ahr", "winzer"]):
                    continue
                city = guess_city_from_text(combined) or "Bad Neuenahr-Ahrweiler"
                ev = make_event(title, parse_date(date_s), parse_date(date_s), "Ahrtal", city,
                                desc, url, source, "ahrtal wein wanderung führung outdoor genuss", 0.9)
                if ev:
                    events.append(ev)
        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return events


def fetch_andernach_highlights() -> list:
    """Andernach official/tourism pages: small-city festivals, castle-garden events, Rhine/outdoor highlights."""
    source = "Andernach"
    urls = [
        "https://www.andernach.de/aktuelles/veranstaltungskalender/",
        "https://www.andernach-begeistert.de/sehen-erleben/veranstaltungen/",
    ]
    events = []
    try:
        for url in urls:
            html = fetch_url(url, timeout=25)
            events.extend(events_from_jsonld(html, source, "Andernach", "andernach kultur markt outdoor", 0.85, url))
            text = clean_html(html)
            # Known tourism highlight format: "NightWash ... am 30.05.26 Open-Air im Schlossgarten".
            for m in re.finditer(r"([^\.]{4,120}?)\s+am\s+(\d{2})\.(\d{2})\.(\d{2,4})([^\.]{0,120})", text, re.I):
                title = clean_html(m.group(1)).split(" ")[-12:]
                title = " ".join(title).strip(" -:;,")
                desc = clean_html(m.group(5))
                if "NightWash" in title or "nightwash" in desc.lower():
                    title = "NightWash Comedy Open-Air im Schlossgarten"
                year = int(m.group(4))
                year = 2000 + year if year < 100 else year
                start_dt = datetime(year, int(m.group(3)), int(m.group(2)))
                if not any(k in f"{title} {desc}".lower() for k in ["open-air", "schlossgarten", "markt", "fest", "film", "nacht", "kulturnacht", "musiktage", "michelsmarkt", "wandern"]):
                    continue
                ev = make_event(title, start_dt, start_dt, "Andernach", "Andernach", desc,
                                url, source, "andernach small-city outdoor festival kultur", 0.8)
                if ev:
                    events.append(ev)
        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return events


def fetch_songkick() -> list:
    """Songkick concert listings for Bonn metro — JSON-LD primary, link fallback."""
    source = "Songkick"
    try:
        url = f"https://www.songkick.com/metro-areas/28447-germany-bonn/{TODAY.year}"
        html = fetch_url(url)
        events = []
        seen_titles = set()

        # Primary: JSON-LD structured data (most reliable)
        ld_blocks = re.findall(r'application/ld\+json">(.*?)</script>', html, re.DOTALL)
        for ld in ld_blocks:
            try:
                data = json.loads(ld)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") != "MusicEvent":
                        continue
                    name = item.get("name", "")
                    start = item.get("startDate", "")
                    venue_data = item.get("location", {})
                    venue_name = venue_data.get("name", "")
                    url_link = item.get("url", "")

                    # Date filter
                    event_date = parse_date(start[:10]) if start else None
                    if event_date and not (TODAY <= event_date <= END_DATE):
                        continue

                    if name in seen_titles:
                        continue
                    seen_titles.add(name)

                    # Detect city from venue name
                    city_guess = guess_city_from_text(venue_name) or "bonn"
                    km = haversine(BONN_LAT, BONN_LON, *coords_for_city(city_guess))
                    if km > MAX_RADIUS_KM:
                        continue

                    # Avoid duplicate venue in title (Songkick often has "Artist @ Venue" already in name)
                    display_title = name
                    if venue_name and f"@ {venue_name}" not in name and venue_name not in name:
                        display_title = f"{name} @ {venue_name}"
                    events.append({
                        "title": display_title,
                        "date": start[:10] if start else "",
                        "time": start[11:16] if len(start) > 11 else "",
                        "venue": venue_name,
                        "city": city_guess.title(),
                        "description": "",
                        "price": "",
                        "link": url_link,
                        "distance_km": round(km, 1),
                        "score": round(distance_score(km) * 1.5, 2),
                        "source": source,
                        "category": "concert",
                    })
            except (json.JSONDecodeError, AttributeError):
                continue

        # Fallback: link scraping (if JSON-LD didn't find enough)
        if len(events) < 3:
            links = re.findall(r'href="(/concerts/[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
            for href, text in links:
                clean = re.sub(r"<[^>]+>", " ", text).strip()
                clean = re.sub(r"\s+", " ", clean)
                if not clean or clean in seen_titles:
                    continue
                seen_titles.add(clean)
                events.append({
                    "title": unescape(clean),
                    "date": "", "time": "", "venue": "",
                    "city": "Bonn area", "description": "", "price": "",
                    "link": f"https://www.songkick.com{href}",
                    "distance_km": 0,
                    "score": round(distance_score(0) * 1.5, 2),
                    "source": source, "category": "concert",
                })

        return events
    except Exception as e:
        print(f"⚠ {source}: {e}", file=sys.stderr)
        return []


def fetch_bundeskunsthalle() -> list:
    """Bundeskunsthalle exhibitions via browserless — known exhibitions + scrape."""
    source = "Bundeskunsthalle"
    events = []

    # Known current exhibitions (reliable, updated by scrape below)
    # These are the permanent links that change ~every 3-6 months
    KNOWN_EXHIBITIONS = {
        "hujar": "Peter Hujar: Eyes Open in the Dark",
        "amazonia": "Amazônia: Indigene Welten",
        "expedition-weltmeere": "Expedition to the World's Oceans",
    }

    try:
        # Direct HTTP — browserless times out on this site.
        # We only verify reachability here and then keep a conservative curated allowlist.
        fetch_url("https://www.bundeskunsthalle.de/en/exhibitions", timeout=20)
    except Exception as e:
        print(f"⚠ {source} (reachability): {e}", file=sys.stderr)

    # Build events from known exhibitions
    for slug, title in KNOWN_EXHIBITIONS.items():
        events.append({
            "title": title,
            "date": "",
            "time": "Tue-Sun 10:00-18:00",
            "venue": "Bundeskunsthalle",
            "city": "Bonn",
            "description": "Museum Mile, Helmut-Kohl-Allee 4",
            "price": "",
            "link": f"https://www.bundeskunsthalle.de/en/{slug}",
            "distance_km": 1.5,
            "score": round(distance_score(1.5) * 1.4, 2),
            "source": source,
            "category": "exhibition",
        })
    return events


def fetch_grok_search() -> list:
    """Agentic Grok web search for obscure local/outdoor events missed by deterministic sources."""
    source = "Grok Search"
    if not XAI_API_KEY:
        print(f"⚠ {source}: No XAI_API_KEY", file=sys.stderr)
        return []
    if os.environ.get("NRW_EVENTS_ENABLE_GROK", "").lower() not in {"1", "true", "yes"}:
        print(f"⚠ {source}: disabled by default; set NRW_EVENTS_ENABLE_GROK=1 for deep agentic fallback", file=sys.stderr)
        return []
    events = []
    # Grok is high-quality but slower/costlier than raw search APIs; keep it narrow so the
    # weekend report stays responsive. Deterministic scrapers do the heavy lifting.
    queries = search_queries()[:2]
    system_prompt = (
        "Find concrete dated events near Bonn, Germany within ~75km. "
        "Prioritize small local/outdoor/province events: Königswinter, Siebengebirge, Ahrtal, Andernach, "
        "hikes, wine walks, markets, festivals, guided tours. Return only a JSON array of objects with "
        "title, date, city, venue, description, url. Exclude static tourism pages without a specific date."
    )
    for query in queries:
        try:
            payload = {
                "model": "grok-4-1-fast",
                "input": [
                    {"role": "developer", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                "tools": [{"type": "web_search"}],
            }
            data = post_json(
                "https://api.x.ai/v1/responses",
                payload,
                timeout=35,
                headers={"Authorization": f"Bearer {XAI_API_KEY}"},
            )
            text_parts = []
            for item in data.get("output", []):
                if item.get("type") == "message" and item.get("role") == "assistant":
                    for part in item.get("content", []):
                        if part.get("type") in {"output_text", "text"} and part.get("text"):
                            text_parts.append(part["text"])
            for candidate in extract_json_array("\n".join(text_parts)):
                if not isinstance(candidate, dict):
                    continue
                title = candidate.get("title") or candidate.get("name") or ""
                link = candidate.get("url") or candidate.get("link") or ""
                desc = " ".join(str(candidate.get(k) or "") for k in ["date", "venue", "description"])
                ev = search_result_event(title, link, desc, source, 0.7)
                if ev:
                    ev["date"] = str(candidate.get("date") or "")[:40]
                    ev["venue"] = str(candidate.get("venue") or "")[:120]
                    ev["city"] = str(candidate.get("city") or ev["city"])[:80].title()
                    events.append(ev)
        except Exception as e:
            print(f"⚠ {source} ({query[:30]}...): {e}", file=sys.stderr)
    return events


def fetch_exa_search() -> list:
    """Exa neural search for event pages; secondary fallback after deterministic/Grok sources."""
    source = "Exa Search"
    if not EXA_API_KEY:
        print(f"⚠ {source}: No EXA_API_KEY", file=sys.stderr)
        return []
    events = []
    # Quantity lever: number of Exa queries (each ~5 results). Raise NRW_EVENTS_EXA_QUERIES to widen.
    exa_n = int(os.environ.get("NRW_EVENTS_EXA_QUERIES", "10"))
    for query in search_queries()[:exa_n]:
        try:
            data = post_json(
                "https://api.exa.ai/search",
                {
                    "query": query,
                    "numResults": 5,
                    "type": "auto",
                    "contents": {"text": {"maxCharacters": 500}},
                },
                timeout=25,
                headers={"x-api-key": EXA_API_KEY},
            )
            for result in data.get("results", []):
                title = result.get("title") or ""
                link = result.get("url") or ""
                desc = result.get("text") or result.get("summary") or ""
                published = result.get("publishedDate") or ""
                ev = search_result_event(title, link, f"{published} {desc}", source, 0.58)
                if ev:
                    events.append(ev)
        except Exception as e:
            print(f"⚠ {source} ({query[:30]}...): {e}", file=sys.stderr)
    return events


# ── Dedup + Scoring ─────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """Normalize for dedup comparison — aggressive to catch near-dupes."""
    t = title.lower().strip()
    if "ahrweinwalk" in t:
        return "ahrweinwalk"
    # Strip common prefixes like "Ausstellung:", "Tickets für", etc.
    t = re.sub(r"^(ausstellung[:\s]*|exhibition[:\s]*|konzert[:\s]*|concert[:\s]*|tickets?\s+für\s+)", "", t)
    # Remove all non-alphanumeric (including umlauts → keep)
    t = re.sub(r"[^a-zäöüß0-9]", "", t)
    return t


def deduplicate(events: list) -> list:
    """Remove duplicates by fuzzy title + city matching."""
    seen = {}
    result = []
    for ev in events:
        norm = normalize_title(ev["title"])
        key = norm if norm == "ahrweinwalk" else norm[:50] + "|" + ev["city"].lower()[:10]
        if key in seen:
            # Keep the one with higher score
            if ev["score"] > seen[key]["score"]:
                result = [e for e in result if ((normalize_title(e["title"]) if normalize_title(e["title"]) == "ahrweinwalk" else normalize_title(e["title"])[:50] + "|" + e["city"].lower()[:10]) != key)]
                result.append(ev)
                seen[key] = ev
        else:
            seen[key] = ev
            result.append(ev)
    return result


# ── Output Formatting ───────────────────────────────────────────────

def format_report(events: list) -> str:
    """Format events into a tighter report ranked by the configured preference weights."""
    lines = []
    lines.append(f"# 🗓 Weekend Event Report")
    lines.append(f"**{TODAY.strftime('%A %d %b')} → {END_DATE.strftime('%A %d %b %Y')}**")
    lines.append(f"**Radius:** {MAX_RADIUS_KM}km from Bonn")
    lines.append(f"**Sources:** {len(set(e['source'] for e in events))} active")
    lines.append(f"**Relevant events after cleanup:** {len(events)}")
    lines.append("")

    def bucket(ev: dict) -> str:
        text = (ev.get("category", "") + " " + ev.get("title", "") + " " + ev.get("description", "")).lower()
        if any(k in text for k in ["techno", "electronic", "party", "dj", "nightlife"]) or re.search(r"\bclub\b", text):
            return "Nightlife & Electronic"
        if any(k in text for k in ["concert", "konzert", "musik", "music", "live"]):
            return "Concerts & Live Music"
        if any(k in text for k in ["führung", "tour", "rundgang", "streetart", "kirschblüte", "kirschbluete", "antikmarkt", "flohmarkt", "markt", "weinwanderung", "wanderung", "walk", "weinberg", "winzer", "weingut", "ahrtal", "stadtteilfest", "straßenfest", "strassenfest", "dorffest", "kirmes", "genussmeile", "weinmeile", "siebengebirge", "kottenforst", "natur"]):
            return "Walks, Markets & Outdoor"
        if any(k in text for k in ["exhibition", "ausstellung", "museum", "gallery", "galerie", "art", "kunst"]):
            return "Exhibitions & Museums"
        if any(k in text for k in ["theater", "comedy", "vortrag", "lecture", "film", "kino", "reading", "meetup", "gaming", "hacker", "opensource"]):
            return "Talks, Community & Culture"
        return "Other"

    preferred_order = [
        "Nightlife & Electronic",
        "Concerts & Live Music",
        "Exhibitions & Museums",
        "Talks, Community & Culture",
        "Walks, Markets & Outdoor",
        "Other",
    ]

    def priority_bonus(ev: dict) -> float:
        text = (ev.get("title", "") + " " + ev.get("category", "") + " " + ev.get("description", "")).lower()
        bonus = 0.0
        if ev.get("source") == "Rheinauen-Flohmarkt":
            bonus += 0.9
        if "flohmarkt" in text:
            bonus += 0.5
        if any(k in text for k in ["ahrweinwalk", "weinwanderung", "ahrtal", "ahrweiler"]):
            bonus += 0.55
        if any(k in text for k in ["stadtteilfest", "straßenfest", "strassenfest", "dorffest", "poppelsdorf", "weinmeile", "genussmeile"]):
            bonus += 0.45
        if "antikmarkt" in text:
            bonus += 0.3
        if ev.get("city") == "Bonn":
            bonus += 0.1
        return bonus

    grouped = {name: [] for name in preferred_order}
    for ev in sorted(events, key=lambda x: (-(x["score"] + priority_bonus(x)), x.get("distance_km", 999), x.get("title", ""))):
        grouped[bucket(ev)].append(ev)

    def format_when(ev: dict) -> str:
        parts = []
        if ev.get("date"):
            parts.append(ev["date"])
        if ev.get("time"):
            parts.append(ev["time"][:60])
        return " ".join(parts).strip()

    # Per-section cap. Default 0 = show ALL events (full output).
    # Set NRW_EVENTS_MAX_PER_SECTION=N to trim for terse contexts (e.g. a digest).
    try:
        max_per_section = int(os.environ.get("NRW_EVENTS_MAX_PER_SECTION", "0"))
    except ValueError:
        max_per_section = 0

    def format_section(title: str, emoji: str, items: list, limit: int = 0):
        if not items:
            return
        cap = max_per_section if max_per_section > 0 else 0
        shown = items if cap <= 0 else items[:cap]
        count_note = f" ({len(items)})" if len(shown) == len(items) else f" ({len(shown)} of {len(items)})"
        lines.append(f"## {emoji} {title}{count_note}")
        lines.append("")
        for ev in shown:
            when = format_when(ev)
            dist_tag = f"{ev['distance_km']}km" if ev.get("distance_km", 0) > 0 else "Bonn"
            score_bar = "★" * max(1, min(5, int(round(ev["score"] * 3))))
            meta = []
            if when:
                meta.append(when)
            if ev.get("venue"):
                meta.append(ev["venue"])
            if ev.get("city"):
                meta.append(ev["city"])
            meta.append(dist_tag)
            meta.append(score_bar)
            lines.append(f"- **{ev['title']}**")
            lines.append(f"  {' · '.join(meta)}")
            if ev.get("description"):
                lines.append(f"  _{ev['description'][:140]}_")
            if ev.get("link"):
                lines.append(f"  🔗 {ev['link']}")
            lines.append("")

    format_section("Nightlife & Electronic", "🌙", grouped["Nightlife & Electronic"])
    format_section("Concerts & Live Music", "🎵", grouped["Concerts & Live Music"])
    format_section("Exhibitions & Museums", "🏛️", grouped["Exhibitions & Museums"])
    format_section("Talks, Community & Culture", "🧠", grouped["Talks, Community & Culture"])
    format_section("Walks, Markets & Outdoor", "🚶", grouped["Walks, Markets & Outdoor"])
    format_section("Other", "📌", grouped["Other"])

    lines.append("---")
    lines.append("### Source Status")
    source_counts = {}
    for e in events:
        source_counts[e["source"]] = source_counts.get(e["source"], 0) + 1
    for src, count in sorted(source_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {src}: {count} events")

    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────

def main():
    print(f"Fetching events for {TODAY.strftime('%d %b')} → {END_DATE.strftime('%d %b %Y')}...", file=sys.stderr)
    print(f"Radius: {MAX_RADIUS_KM}km from Bonn", file=sys.stderr)

    # Optionally load a .env file so the script also works when run directly without
    # the shell wrapper. Real environment variables always take precedence.
    # Lookup order: NRW_EVENTS_ENV_FILE, repo-root .env, current-directory .env.
    global XAI_API_KEY, EXA_API_KEY
    repo_root = Path(__file__).resolve().parents[1]
    for env_path in (
        os.environ.get("NRW_EVENTS_ENV_FILE", ""),
        str(repo_root / ".env"),
        str(Path.cwd() / ".env"),
    ):
        if not env_path or not os.path.exists(env_path):
            continue
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = value
        break
    # Re-read after loading
    XAI_API_KEY = os.environ.get("XAI_API_KEY", "")
    EXA_API_KEY = os.environ.get("EXA_API_KEY", "")

    fetchers = {
        "Köln API": fetch_koeln_api,
        "Bonn HTML": fetch_bonn_html,
        "Bonn.de RSS": fetch_bonn_rss,
        "Harmonie Bonn": fetch_harmonie_bonn,
        "Meetup": fetch_meetup_groups,
        "Rheinauen-Flohmarkt": fetch_rheinaue_flohmarkt,
        "Bonn local recurring": fetch_bonn_local_recurring,
        "Ahrtal": fetch_ahrtal_highlights,
        "Ahrtal calendar": fetch_ahrtal_calendar,
        "Königswinter": fetch_koenigswinter_calendar,
        "VVS Siebengebirge": fetch_vvs_siebengebirge,
        "Tourismus Siebengebirge": fetch_siebengebirge_tourismus,
        "Andernach": fetch_andernach_highlights,
        "Bonn.jetzt": fetch_bonn_jetzt,
        "Songkick": fetch_songkick,
        "Bundeskunsthalle": fetch_bundeskunsthalle,
        "Grok Search": fetch_grok_search,
        "Exa Search": fetch_exa_search,
    }

    all_events = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn): name for name, fn in fetchers.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                print(f"  ✓ {name}: {len(result)} events", file=sys.stderr)
                all_events.extend(result)
            except Exception as e:
                print(f"  ✗ {name}: {e}", file=sys.stderr)

    # Filter low-score and obvious junk before dedup.
    # Floor is intentionally low (quantity-over-quality) so you can filter the full
    # list yourself. Raise NRW_EVENTS_SCORE_FLOOR via env to tighten.
    score_floor = float(os.environ.get("NRW_EVENTS_SCORE_FLOOR", "0.4"))
    filtered = [e for e in all_events if e["score"] >= score_floor and not is_junk_event(e)]
    print(f"\nPre-dedup: {len(filtered)} events (filtered {len(all_events) - len(filtered)} low-score/junk)", file=sys.stderr)

    # Deduplicate
    deduped = deduplicate(filtered)
    print(f"Post-dedup: {len(deduped)} events", file=sys.stderr)

    # Generate report
    report = format_report(deduped)
    print(report)

    # Also save JSON for programmatic use
    json_path = "/tmp/nrw-events-latest.json"
    with open(json_path, "w") as f:
        json.dump(sorted(deduped, key=lambda x: -x["score"]), f, ensure_ascii=False, indent=2)
    print(f"\nJSON saved: {json_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
