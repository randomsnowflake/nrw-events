"""
Shared "tech" layer for the NRW event aggregator.

This module holds every piece of generic machinery that the per-source fetchers
reuse: HTTP, HTML/JSON-LD/iCal parsing, German/English date parsing, geo +
distance scoring, the central ``make_event`` builder, and the junk filter.

Source files in ``sources/`` should contain *only* the logic specific to one
website. Anything reusable belongs here.

Date window: the report window (``TODAY`` … ``END_DATE``) is module-global state
set once by the runner via :func:`set_window`. Always reference it as
``common.TODAY`` / ``common.END_DATE`` so sources see the configured window.
"""

import json
import math
import os
import random
import re
import urllib.request
import urllib.parse  # noqa: F401  (re-exported for sources that build URLs)
import urllib.error
from datetime import datetime, timedelta
from html import unescape
from typing import Optional

from . import category_taxonomy, config

# ── Report window (set by the runner at startup) ────────────────────
DAYS_AHEAD = 3
TODAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
END_DATE = TODAY + timedelta(days=max(DAYS_AHEAD - 1, 0))


def set_window(days_ahead: int) -> None:
    """Configure the look-ahead window. Call once before running fetchers."""
    global DAYS_AHEAD, TODAY, END_DATE
    DAYS_AHEAD = max(int(days_ahead), 1)
    TODAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    END_DATE = TODAY + timedelta(days=max(DAYS_AHEAD - 1, 0))


# ── Month name maps (shared by every date parser) ───────────────────
MONTH_DE = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3,
    "mär": 3, "mae": 3, "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6,
    "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}
MONTH_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# Re-export common config values for convenience.
BONN_LAT, BONN_LON = config.BONN_LAT, config.BONN_LON
MAX_RADIUS_KM = config.MAX_RADIUS_KM


# ── Geo + scoring ───────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two lat/lon points."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def distance_score(km: float) -> float:
    """Score 0.1–1.0 by distance. 0km=1.0, 25km≈0.8, 50km≈0.5, 75km≈0.3."""
    if km <= 0:
        return 1.0
    return max(0.1, 1.0 - (km / MAX_RADIUS_KM) * 0.9)


def category_score(text: str) -> float:
    """Preference score from keyword matching. Kids-only events are capped."""
    text_lower = text.lower()
    negative_keywords = {"kinder", "kids", "grundschüler", "grundschueler", "familie",
                         "family", "vorlesen", "basteln", "jugendliche", "babys",
                         "spielgruppe", "krabbelgruppe", "eltern-kind"}
    adult_outdoor_signals = {
        "wein", "wine", "winzer", "weingut", "afterwalk", "genuss", "lounge",
        "beats", "festival", "markt", "flohmarkt", "street food", "kulinar",
        "stadtteilfest", "straßenfest", "strassenfest", "dorffest", "kirmes",
        "viertel", "meile",
    }
    has_negative = any(neg in text_lower for neg in negative_keywords)
    has_adult_signal = any(sig in text_lower for sig in adult_outdoor_signals)
    if has_negative and not has_adult_signal:
        return 0.25
    best = 0.8  # default
    for keyword, weight in config.CATEGORY_WEIGHT.items():
        if keyword in text_lower:
            best = max(best, weight)
    return best


def coords_for_city(city: str) -> tuple:
    """Coordinates for a city name, defaulting to Bonn center."""
    return config.VENUE_COORDS.get((city or "").lower(), (BONN_LAT, BONN_LON))


def guess_city_from_text(text: str) -> Optional[str]:
    """Extract a known town from free text, preferring specific towns over 'Bonn'."""
    text_lower = re.sub(r"bundesstadt\s+bonn", " ", (text or "").lower())
    # Longer/more-specific names first; Bonn last so a trailing publisher brand
    # ("… Siebengebirge | Bundesstadt Bonn") is not mis-scored as 0 km.
    cities = sorted(config.VENUE_COORDS, key=lambda c: (c == "bonn", -len(c)))
    for city in cities:
        if re.search(rf"(?<![a-zäöüß]){re.escape(city)}(?![a-zäöüß])", text_lower):
            return city
    return None


# ── HTTP ────────────────────────────────────────────────────────────

_BROWSER_PROFILES = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Sec-CH-UA": (
            '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"'
        ),
        "Sec-CH-UA-Platform": '"Windows"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Sec-CH-UA": (
            '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"'
        ),
        "Sec-CH-UA-Platform": '"macOS"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Sec-CH-UA": (
            '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"'
        ),
        "Sec-CH-UA-Platform": '"Linux"',
    },
]
_BROWSER_PROFILE = random.SystemRandom().choice(_BROWSER_PROFILES)


def browser_headers(
    *,
    accept: str,
    sec_fetch_mode: str,
    sec_fetch_dest: str,
    extra: Optional[dict] = None,
) -> dict:
    """Return realistic browser request headers for public event-source fetches.

    The default urllib user agent advertises Python and is easy for sites to
    reject. Use one coherent browser profile for the whole process instead of
    changing identity on every request; callers can still override individual
    headers when a feed/API needs a source-specific ``Accept`` or auth header.
    """
    hdrs = {
        **_BROWSER_PROFILE,
        "User-Agent": os.environ.get("NRW_EVENTS_USER_AGENT", _BROWSER_PROFILE["User-Agent"]),
        "Accept": accept,
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-CH-UA-Mobile": "?0",
        "Sec-Fetch-Dest": sec_fetch_dest,
        "Sec-Fetch-Mode": sec_fetch_mode,
        "Sec-Fetch-Site": "none",
    }
    if sec_fetch_mode == "navigate":
        hdrs["Upgrade-Insecure-Requests"] = "1"
        hdrs["Sec-Fetch-User"] = "?1"
    if extra:
        hdrs.update(extra)
    return hdrs


def fetch_url(
    url: str,
    timeout: int = 15,
    headers: Optional[dict] = None,
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    sec_fetch_mode: str = "navigate",
    sec_fetch_dest: str = "document",
) -> str:
    """GET a URL and return decoded text. Raises on network/HTTP error.

    Defaults model a browser document navigation for HTML event pages. Feed/API
    callers should pass a content-specific ``accept`` value so negotiating
    endpoints do not return their human HTML fallback instead of data.
    """
    hdrs = browser_headers(
        accept=accept,
        sec_fetch_mode=sec_fetch_mode,
        sec_fetch_dest=sec_fetch_dest,
        extra=headers,
    )
    req = urllib.request.Request(url, headers=hdrs)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp.read().decode("utf-8", "ignore")


def post_json(url: str, payload: dict, timeout: int = 45, headers: Optional[dict] = None) -> dict:
    """POST a JSON body and parse the JSON response."""
    hdrs = browser_headers(
        accept="application/json",
        sec_fetch_mode="cors",
        sec_fetch_dest="empty",
        extra={"Content-Type": "application/json", **(headers or {})},
    )
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


# ── HTML / text ─────────────────────────────────────────────────────

def clean_html(text: str) -> str:
    """Strip tags/entities and collapse whitespace."""
    text = unescape(text or "")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_url(url: str) -> str:
    """Decode HTML entities and make internationalized hostnames link-safe."""
    url = unescape(url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return url

    try:
        host = parts.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return url

    userinfo = ""
    if "@" in parts.netloc:
        userinfo = parts.netloc.rsplit("@", 1)[0] + "@"

    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    return urllib.parse.urlunsplit((parts.scheme, f"{userinfo}{host}{port}", parts.path, parts.query, parts.fragment))


def is_raw_api_url(url: str) -> bool:
    """True when an event link points to machine data rather than a human page."""
    parts = urllib.parse.urlsplit(url or "")
    path = (parts.path or "").lower()
    query = (parts.query or "").lower()
    if path.endswith((".json", ".xml")):
        return True
    if "/api/" in path or path.startswith("/api"):
        return True
    if path in {"", "/"} and query and any(bit in query for bit in ("format=json", "output=json", "type=json", "eventid=")):
        return True
    return False


def normalize_venue_name(value: str) -> str:
    """Clean venue text and fix obvious casing/known town typos."""
    cleaned = clean_html(value)[:120]
    if cleaned and cleaned == cleaned.lower():
        cleaned = cleaned.title()
    replacements = {
        "remagen": "Remagen",
    }
    for wrong, right in replacements.items():
        cleaned = re.sub(rf"\b{re.escape(wrong)}\b", right, cleaned, flags=re.IGNORECASE)
    return cleaned


_CANCELLED_STATUS_WORDS = r"abgesagt|entfällt|entfaellt|fällt\s+aus|faellt\s+aus|verschoben"
_CANCELLED_STATUS_SUBJECTS = (
    r"veranstaltung|termin|event|konzert|lesung|theaterabend|show|kurs|workshop|"
    r"führung|fuehrung|rundgang"
)
_CANCELLED_TITLE_PATTERN = re.compile(
    rf"^\s*[-–—:()]*\s*(?:{_CANCELLED_STATUS_WORDS})\b"
    rf"|\b(?:{_CANCELLED_STATUS_WORDS})\b\s*[-–—:()]*$",
    re.IGNORECASE,
)
_CANCELLED_CONTEXT_PATTERN = re.compile(
    rf"\b(?:{_CANCELLED_STATUS_SUBJECTS})\b[^\n.!?]{{0,80}}\b(?:{_CANCELLED_STATUS_WORDS})\b"
    rf"|\b(?:{_CANCELLED_STATUS_WORDS})\b[^\n.!?]{{0,80}}\b(?:krankheitsbedingt|neuer\s+termin|nachgeholt)\b",
    re.IGNORECASE,
)


def has_cancelled_status(title: str, description: str) -> bool:
    """True when text marks this event as cancelled/postponed."""
    return bool(
        _CANCELLED_TITLE_PATTERN.search(title or "")
        or _CANCELLED_CONTEXT_PATTERN.search(description or "")
    )


# ── Date parsing ────────────────────────────────────────────────────

def parse_iso_date(text: str) -> Optional[datetime]:
    """Parse an ISO-ish datetime, dropping timezone (we only care about local date/time)."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def parse_date(text: str) -> Optional[datetime]:
    """Parse many date formats incl. ranges and German month names."""
    text = (text or "").strip()
    if not text:
        return None
    # For ranges, parse the first date.
    text = re.split(r"\s*(?:–|\bbis\b)\s*", text, maxsplit=1)[0].strip()
    text = re.sub(r"^(?:mo|di|mi|do|fr|sa|so|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\.?,?\s*",
                  "", text, flags=re.I)
    for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%a, %d %b %Y %H:%M:%S %z"]:
        try:
            return datetime.strptime(text[:len(fmt) + 5], fmt).replace(tzinfo=None)
        except (ValueError, IndexError):
            continue
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", text)
    if m:
        day, mon, year = map(int, m.groups())
        try:
            return datetime(year, mon, day)
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(20\d{2})", text)
    if m:
        day, mon, year = m.groups()
        mon_num = MONTH_DE.get(mon.lower())
        if mon_num:
            return datetime(int(year), mon_num, int(day))
    m = re.search(r"(\d{1,2})\s+([A-Za-zäöüÄÖÜ]+)\s*(20\d{2})", text)
    if m:
        day, mon, year = m.groups()
        key = mon.lower().rstrip(".")
        mon_num = MONTH_DE.get(key) or MONTH_EN.get(key)
        mon_num = mon_num or {
            "jan": 1, "feb": 2, "mar": 3, "mär": 3, "maerz": 3, "apr": 4,
            "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
            "okt": 10, "nov": 11, "dec": 12, "dez": 12,
        }.get(key)
        if mon_num:
            return datetime(int(year), mon_num, int(day))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def extract_dates(text: str) -> list:
    """Extract parseable dates from free text (for search-result filtering)."""
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


def date_range_overlaps(dates: list) -> bool:
    """True if any extracted date is inside the window; empty list = unknown = include."""
    if not dates:
        return True
    return any(TODAY <= dt <= END_DATE for dt in dates)


def in_date_range(date_str: str) -> bool:
    """True if a date string is in-window, or unparseable (include-when-unknown)."""
    dt = parse_date(date_str)
    if dt is None:
        return True
    return TODAY <= dt <= END_DATE


_TIME_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _round_time_to_quarter(hour: int, minute: int) -> tuple[int, int]:
    total = hour * 60 + minute
    rounded = int(round(total / 15) * 15) % (24 * 60)
    return divmod(rounded, 60)


def _format_hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def sanitize_time_text(time_text: str) -> str:
    """Suppress common scraper time artifacts while preserving useful ranges."""
    text = (time_text or "").strip()
    if not text:
        return text

    matches = list(_TIME_PATTERN.finditer(text))
    if not matches:
        return text

    parsed = [(int(match.group(1)), int(match.group(2))) for match in matches[:2]]
    start_hour, start_minute = parsed[0]
    if start_hour > 23 or start_minute > 59:
        return text

    rounded_start = (start_hour, start_minute)
    has_odd_start = start_minute % 5 != 0
    if has_odd_start:
        rounded_start = _round_time_to_quarter(start_hour, start_minute)

    if len(parsed) >= 2:
        end_hour, end_minute = parsed[1]
        if end_hour > 23 or end_minute > 59:
            return _format_hhmm(*rounded_start)
        start_total = start_hour * 60 + start_minute
        end_total = end_hour * 60 + end_minute
        if end_total < start_total:
            end_total += 24 * 60
        duration = end_total - start_total
        if (end_hour, end_minute) in {(23, 59), (0, 0)} or duration < 20 or end_minute % 5 != 0:
            return _format_hhmm(*rounded_start)
        if " bis " in text:
            separator = " bis "
        elif "-" in text:
            separator = "-"
        else:
            separator = "–"
        return f"{_format_hhmm(*rounded_start)}{separator}{_format_hhmm(end_hour, end_minute)}"

    return _format_hhmm(*rounded_start)


# ── Event construction + junk filter ────────────────────────────────

def make_event(title: str, start_dt: Optional[datetime], end_dt: Optional[datetime],
               venue: str, city: str, description: str, link: str, source: str,
               category: str, trust: float = 1.0, time_text: str = "",
               coords: Optional[tuple] = None) -> Optional[dict]:
    """Build a scored event dict and apply window + radius + junk checks.

    ``coords`` optionally pins the event to an explicit (lat, lon) — e.g. a venue
    point — instead of deriving it from ``city`` via :func:`coords_for_city`.
    """
    if not title:
        return None
    if start_dt and end_dt and (end_dt < TODAY or start_dt > END_DATE):
        return None
    if start_dt and not end_dt and not (TODAY <= start_dt <= END_DATE):
        return None
    km = haversine(BONN_LAT, BONN_LON, *(coords or coords_for_city(city)))
    if km > MAX_RADIUS_KM:
        return None
    if start_dt and end_dt and start_dt.date() != end_dt.date():
        if start_dt < TODAY <= end_dt:
            date_text = f"ongoing until {end_dt.strftime('%Y-%m-%d')}"
        else:
            date_text = f"{start_dt.strftime('%Y-%m-%d')}–{end_dt.strftime('%Y-%m-%d')}"
    elif start_dt:
        date_text = start_dt.strftime("%Y-%m-%d")
    else:
        date_text = ""
    if not time_text and start_dt and (start_dt.hour or start_dt.minute):
        time_text = start_dt.strftime("%H:%M")
        if end_dt and (end_dt.hour or end_dt.minute):
            time_text += "–" + end_dt.strftime("%H:%M")
    time_text = sanitize_time_text(time_text)
    full_text = f"{title} {venue} {city} {description} {category}"
    canonical_category = category_taxonomy.categorize_event(category, title, f"{description} {link}")
    event_link = normalize_url(link)
    if is_raw_api_url(event_link):
        event_link = ""
    ev = {
        "title": clean_html(title),
        "date": date_text,
        "time": time_text,
        "venue": normalize_venue_name(venue),
        "city": clean_html(city).title(),
        "description": clean_html(description),
        "price": "",
        "link": event_link,
        "distance_km": round(km, 1),
        "score": round(distance_score(km) * category_score(full_text) * trust, 2),
        "source": source,
        "category": category,
        "category_key": canonical_category["key"],
        "category_label": canonical_category["label"],
        "category_confidence": canonical_category.get("confidence", 0),
        "category_reason": canonical_category.get("reason", ""),
    }
    return None if is_junk_event(ev) else ev


def is_junk_event(ev: dict) -> bool:
    """Suppress legal pages, stale entries, classes, and low-signal sludge."""
    title = (ev.get("title") or "").lower()
    desc = (ev.get("description") or "").lower()
    venue = (ev.get("venue") or "").lower()
    link = (ev.get("link") or "").lower()
    text = f"{title} {desc} {venue} {link}"

    # Stale entries with a parseable out-of-window date. Date *ranges*
    # ("start–end", en-dash) are kept whenever the span overlaps the window —
    # e.g. a flea-market season or a months-long exhibition that started before
    # today is still current and must not be dropped as "stale".
    date_str = ev.get("date") or ""
    if "–" in date_str:
        parts = date_str.split("–", 1)
        sdt, edt = parse_date(parts[0]), parse_date(parts[1])
        if sdt and edt and (edt < TODAY or sdt > END_DATE):
            return True
    else:
        dt = parse_date(date_str)
        if dt and not (TODAY <= dt <= END_DATE):
            return True

    junk_title_bits = {
        "privacy policy", "faq", "frequently asked questions", "contact", "kontakt",
        "imprint", "impressum", "corruption prevention", "accessibility statement",
        "newsletter", "jobs", "sitemap", "terms of use", "datenschutz",
        "veranstaltungen aktuell", "auf einen blick", "10 best", "the best events",
        "alle veranstaltungen", "veranstaltungskalender", "event calendar",
    }
    if any(bit in title for bit in junk_title_bits):
        return True

    junk_link_bits = {
        "/privacy", "/faq", "/contact", "/imprint", "/jobs", "/search", "/sitemap",
        "eventim.de/city", "livegigs.de", "news.de/lokales", "/metro-areas/",
    }
    if any(bit in link for bit in junk_link_bits):
        return True

    if "grüne jugend" in text or "gruene jugend" in text:
        return True

    if has_cancelled_status(ev.get("title") or "", ev.get("description") or ""):
        return True

    hard_block_bits = {
        # Static attraction pages and routine social meetups kept leaking from
        # municipal calendars as if they were one-off events. These are not
        # useful destination listings for veranstaltungen-bonn.de.
        "phantasialand",
        "phantasia land",
        "phantasia-land",
    }
    if any(bit in text for bit in hard_block_bits):
        return True

    routine_or_political_bits = {
        "ausschuss", "ausschusssitzung", "beirat", "bürgerfragestunde", "buergerfragestunde",
        "fraktion", "infostand", "kreistag", "mitgliederversammlung", "ortsbeirat", "parteitag",
        "ratssitzung", "ratsinformationssystem", "seniorenbeirat", "seniorenvertretung", "sitzung",
        "sprechstunde", "sprechtag", "stadtrat", "stadtverordnete", "tagesordnung",
        "wahlkampf", "wahlstand",
    }
    routine_phrase_bits = {
        "regelmäßig", "regelmaessig", "wöchentlich", "woechentlich", "wiederkehrend",
        "frauentreff", "handarbeitstreff", "frühstückstreff", "fruehstueckstreff", "frühstückszeit",
        "fruehstueckszeit", "frauenfrühstück", "frauenfruehstueck", "häkel-treff", "haekel-treff", "kindertreff", "offener treff",
        "offener puzzle-treff", "offenes ohr", "klaaferei", "seniorencafe", "seniorencafé",
        "seniorennachmittag", "seniorengymnastik", "spielezeit", "stammtisch", "stricken und klönen",
        "stricken und kloenen", "treffen der bad honnefer funkamateure",
        "treffen pflegender angehöriger", "treffen pflegender angehoeriger",
        "veranstaltung der senioreninformation",
    }
    cultural_event_bits = {
        "ausstellung", "festival", "flohmarkt", "kabarett", "konzert", "kunstmarkt",
        "lesung", "live-musik", "museum", "theater", "vernissage",
        "tag der offenen tür", "tag der offenen tuer",
    }
    if (any(bit in text for bit in routine_or_political_bits)
            and not any(bit in text for bit in cultural_event_bits)):
        return True
    if any(bit in text for bit in routine_phrase_bits) and not any(
        bit in text for bit in cultural_event_bits
    ):
        return True

    regular_low_value_bits = {
        # Recurring basic markets are useful civic infrastructure, not a
        # destination-worthy event for this report. Keep explicit flea/special
        # markets covered by the normal market/festival signals.
        "wochenmarkt",
    }
    destination_market_bits = {
        "antikmarkt", "feierabendmarkt", "festival", "flohmarkt", "jahrmarkt",
        "kirmes", "kunstmarkt", "spezialmarkt", "stadtteilfest", "strassenfest",
        "straßenfest", "street food", "trödelmarkt", "troedelmarkt", "weihnachtsmarkt",
    }
    if (any(bit in text for bit in regular_low_value_bits)
            and not any(bit in text for bit in destination_market_bits)):
        return True

    generic_low_value_bits = {
        "fortgeschrittene", "sprachkurs", "italienisch", "französisch", "englischkurs",
        "yogakurs", "offene sprechstunde", "beratung", "frauen in bewegung",
        "gedächtnistraining", "gedaechtnistraining", "deutschkurs", "pilates-training",
        "sitzgymnastik", "rückbildungsgymnastik", "rueckbildungsgymnastik",
        "wirbelsäulengymnastik", "wirbelsaeulengymnastik", "patientenveranstaltung",
        "english club am vormittag", "gymnastik mal", "yoga mit kleinkindern",
    }
    if any(bit in text for bit in generic_low_value_bits):
        return True

    # Web-search results are noisy: require topical + date/event signal, since they
    # also return static venue/shop/route pages.
    if ev.get("source") in {"Exa Search", "Grok Search"}:
        strong_signal = any(k in text for k in [
            "konzert", "concert", "ausstellung", "museum", "festival", "party", "dj",
            "techno", "electronic", "führung", "tour", "theater", "comedy", "lesung",
            "wein", "winzer", "weingut", "wanderung", "wandern", "wander", "walk",
            "ahrtal", "ahrweiler", "stadtteilfest", "straßenfest", "strassenfest",
            "dorffest", "kirmes", "poppelsdorf", "endenich", "beuel", "bad godesberg",
            "siebengebirge", "königswinter", "koenigswinter", "drachenfels",
            "petersberg", "heisterbach", "andernach", "namedy", "linz", "unkel",
            "remagen", "rolandseck", "bad honnef", "dernau", "mayschoss", "altenahr",
            "walporzheim", "weinprobe", "weinfest", "kottenforst", "natur", "rundgang",
            "genussmeile", "weinmeile",
        ])
        explicit_local_event = any(k in text for k in [
            "weinmeile", "genussmeile", "stadtteilfest", "straßenfest", "strassenfest",
            "dorffest", "kirmes", "weinfest", "wirtefestival", "promenadenfest",
        ])
        date_signal = bool(re.search(
            r"\b(20\d{2}|\d{1,2}\.\d{1,2}\.|\d{1,2}\s*(?:jan|feb|mär|mae|apr|mai|jun|jul|aug|sep|okt|nov|dez)|"
            r"montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|wochenende|heute|morgen|am\s+\d)",
            text, re.IGNORECASE,
        ))
        static_page_bits = [
            "öffnungszeiten", "route planen", "unser sortiment", "wanderwege in der nähe",
            "die besten", "wiki", "website", "hotels", "immobilien",
        ]
        if any(bit in text for bit in static_page_bits) and not explicit_local_event:
            return True
        if not strong_signal or (not date_signal and not explicit_local_event):
            return True

    return False


# ── JSON-LD (schema.org) ────────────────────────────────────────────

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
            if any(t and "Event" in str(t) for t in types):
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


def _jsonld_location(loc) -> tuple:
    """Return (venue_name, city) from a schema.org location that may be a dict or list."""
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if not isinstance(loc, dict):
        return "", ""
    venue = loc.get("name", "") or ""
    address = loc.get("address", {})
    city = ""
    if isinstance(address, dict):
        city = address.get("addressLocality") or ""
    city = re.sub(r"^\d{5}\s+", "", str(city)).strip()
    return venue, city


def _jsonld_schedule_items(schedule) -> list:
    """Return schema.org Schedule objects as a list, preserving source order."""
    if isinstance(schedule, list):
        return [s for s in schedule if isinstance(s, dict)]
    if isinstance(schedule, dict):
        return [schedule]
    return []


def _jsonld_schedule_dt(schedule: dict, date_key: str, time_key: str = "") -> Optional[datetime]:
    """Parse a Schedule date and optional time into a naive datetime."""
    dt = parse_iso_date(schedule.get(date_key, ""))
    if not dt:
        return None
    time_value = (schedule.get(time_key, "") if time_key else "") or ""
    m = re.match(r"^(\d{1,2}):(\d{2})", str(time_value).strip())
    if m:
        hour, minute = map(int, m.groups())
        dt = dt.replace(hour=hour, minute=minute)
    return dt


def _jsonld_schedule_time_text(schedule: dict) -> str:
    """Return a compact display time from schema.org Schedule start/end times."""
    start = str(schedule.get("startTime", "") or "").strip()
    end = str(schedule.get("endTime", "") or "").strip()
    if start and end:
        return f"{start}–{end}"
    return start or end


def events_from_jsonld(html: str, source: str, default_city: str, category: str,
                       trust: float, default_link: str) -> list:
    """Build events from every schema.org Event in a page's JSON-LD."""
    events = []
    for item in jsonld_event_items(html):
        title = item.get("name", "")
        start_dt = parse_iso_date(item.get("startDate", ""))
        end_dt = parse_iso_date(item.get("endDate", "")) or start_dt
        venue, city = _jsonld_location(item.get("location"))
        city = city or default_city
        desc = item.get("description", "")
        link = item.get("url") or default_link

        schedules = _jsonld_schedule_items(item.get("eventSchedule"))
        if schedules:
            for schedule in schedules:
                sched_start = _jsonld_schedule_dt(schedule, "startDate", "startTime")
                sched_end = _jsonld_schedule_dt(schedule, "endDate", "endTime") or sched_start
                ev = make_event(
                    title, sched_start, sched_end, venue, city, desc, link, source,
                    category, trust, time_text=_jsonld_schedule_time_text(schedule),
                )
                if ev:
                    events.append(ev)
            # Explicit schedule entries are the real appointments. The top-level
            # start/end often describes only a season span, e.g. Rheinauen-Flohmarkt
            # April→October, and must not be emitted as a stale appointment.
            continue

        ev = make_event(title, start_dt, end_dt, venue, city, desc, link, source, category, trust)
        if ev:
            events.append(ev)
    return events


# ── iCal (RFC 5545) ─────────────────────────────────────────────────
# Many German venues run WordPress + "The Events Calendar" (Tribe), exposing a
# clean .ics feed at ?post_type=tribe_events&ical=1. iCal beats HTML scraping.

def _ical_unfold(text: str) -> str:
    """RFC 5545 line unfolding: CRLF + space/tab continues the previous line."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _ical_unescape(text: str) -> str:
    return (text.replace("\\n", " ").replace("\\N", " ")
                .replace('\\"', '"')
                .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")).strip()


def events_from_time_listing(html: str, source: str, default_city: str, category: str,
                             trust: float, base_url: str, min_title: int = 6,
                             max_chars: int = 900, anchor_pattern: Optional[str] = None) -> list:
    """Scrape a server-rendered listing that pairs ``<time datetime="…">`` tags with
    nearby title links — common in TYPO3 ``tx_news`` / municipal calendars that
    expose no iCal or JSON-LD feed. Each ``<time>`` is matched to the closest
    in-document anchor (within ``max_chars``) whose text looks like a real title.

    By default every ``<a>`` is a title candidate, filtered by a denylist of
    navigation labels. Pass ``anchor_pattern`` (a regex capturing href + inner
    text) to scope candidates to a CMS-specific title wrapper instead, e.g.
    ``result-list_object-title…<a href="(…)">(…)</a>``. Fails soft on unexpected
    markup (returns the events it could pair, or []).
    """
    times = [(m.start(), m.group(1)) for m in re.finditer(r'<time[^>]*datetime="([^"]+)"', html)]
    pattern = anchor_pattern or r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    anchors = [(m.start(), m.group(1), clean_html(m.group(2)))
               for m in re.finditer(pattern, html, re.S | re.I)]
    # A scoped title pattern already excludes nav links; only the broad default
    # needs the denylist.
    bad = () if anchor_pattern else (
        "drucken", "session.", "weiterlesen", "mehr ", "mehr:", "details",
        "zum kalender", "veranstaltungsliste", "impressum", "anmelden", "suche")
    events, seen = [], set()
    for tp, dt in times:
        cand = sorted((abs(ap - tp), href, t) for ap, href, t in anchors
                      if abs(ap - tp) < max_chars and len(t) >= min_title
                      and not any(b in t.lower() for b in bad))
        if not cand:
            continue
        _, href, title = cand[0]
        key = (title.lower(), dt[:10])
        if key in seen:
            continue
        seen.add(key)
        start = parse_iso_date(dt)
        link = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        ev = make_event(title, start, None, "", default_city, "", link, source, category, trust)
        if ev:
            events.append(ev)
    return events


def events_from_ecmaps_tiles(html: str, source: str, default_city: str, category: str,
                             trust: float, base_url: str) -> list:
    """Parse destination.one / ECMaps tile listings with date, title, and venue.

    Used by regional tourism calendars such as Naturregion Sieg. The markup is
    server-rendered but minified, so this intentionally pairs fields inside the
    tile anchor instead of relying on line structure.
    """
    events, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(?P<href>[^"]+)"[^>]*class="[^"]*tile__link[^"]*"[^>]*>(?P<body>.*?)</a>',
                         html, re.S | re.I):
        href = m.group("href")
        body = m.group("body")
        if "${" in href or "${" in body:
            continue
        date_m = re.search(r'tile__label-text[^>]*>\s*(.*?)\s*</span>', body, re.S | re.I)
        title_m = re.search(r'header__head[^>]*>\s*(.*?)\s*</p>', body, re.S | re.I)
        venue_m = re.search(r'icontext__text[^>]*>\s*(.*?)\s*</span>', body, re.S | re.I)
        if not (date_m and title_m):
            continue
        title = clean_html(title_m.group(1))
        date_text = clean_html(date_m.group(1))
        venue = clean_html(venue_m.group(1) if venue_m else "")
        start = parse_date(date_text)
        city = guess_city_from_text(venue) or default_city
        key = (title.lower(), start.strftime("%Y-%m-%d") if start else date_text)
        if key in seen:
            continue
        seen.add(key)
        ev = make_event(
            title, start, start, venue, city, "", urllib.parse.urljoin(base_url, href),
            source, category, trust,
        )
        if ev:
            events.append(ev)
    return events


def _wp_event_manager_datetimes(text: str) -> tuple:
    text = re.sub(r"\s+", " ", clean_html(text))
    m = re.search(
        r"(?P<start>\d{1,2}\.\d{1,2}\.20\d{2})(?:\s*@\s*(?P<stime>\d{1,2}:\d{2}))?"
        r"(?:\s*-\s*(?P<end>\d{1,2}\.\d{1,2}\.20\d{2})?\s*@?\s*(?P<etime>\d{1,2}:\d{2})?)?",
        text,
    )
    if not m:
        return None, None, ""
    start = parse_date(m.group("start"))
    end = parse_date(m.group("end") or m.group("start"))
    stime, etime = m.group("stime"), m.group("etime")
    if start and stime:
        hour, minute = map(int, stime.split(":"))
        start = start.replace(hour=hour, minute=minute)
    if end and etime:
        hour, minute = map(int, etime.split(":"))
        end = end.replace(hour=hour, minute=minute)
    time_text = f"{stime}-{etime}" if stime and etime else (stime or "")
    return start, end, time_text


def events_from_wp_event_manager_listing(html: str, source: str, category: str, trust: float) -> list:
    """Parse WP Event Manager list cards, skipping locations outside known towns."""
    events, seen = [], set()
    for m in re.finditer(r'<div class="event_listing\b(?P<body>.*?)</a>', html, re.S | re.I):
        body = m.group("body")
        href_m = re.search(r'<a[^>]+href="([^"]+)"', body, re.S | re.I)
        title_m = re.search(r'wpem-event-title.*?<h3[^>]*>(.*?)</h3>', body, re.S | re.I)
        date_m = re.search(r'wpem-event-date-time.*?<span[^>]*>(.*?)</span>', body, re.S | re.I)
        loc_m = re.search(r'wpem-event-location.*?<span[^>]*>(.*?)</span>', body, re.S | re.I)
        if not (href_m and title_m and date_m and loc_m):
            continue
        title = clean_html(title_m.group(1))
        location = clean_html(loc_m.group(1))
        city = guess_city_from_text(location)
        if not city:
            continue
        start, end, time_text = _wp_event_manager_datetimes(date_m.group(1))
        key = (title.lower(), start.strftime("%Y-%m-%d") if start else "")
        if key in seen:
            continue
        seen.add(key)
        ev = make_event(
            title, start, end, location, city, "", href_m.group(1),
            source, category, trust, time_text=time_text,
        )
        if ev:
            events.append(ev)
    return events


def _ical_content_line(line: str) -> tuple:
    """Split an iCal content line at the first colon outside quoted params."""
    in_quote = False
    for idx, char in enumerate(line):
        if char == '"':
            in_quote = not in_quote
        elif char == ":" and not in_quote:
            return line[:idx], line[idx + 1:]
    return line, ""


def _ical_parse_dt(value: str) -> Optional[datetime]:
    v = (value or "").strip()
    if re.match(r"^\d{8}T\d{6}Z?$", v):
        return datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if re.match(r"^\d{8}T\d{4}Z?$", v):
        return datetime.strptime(v[:13], "%Y%m%dT%H%M")
    if re.match(r"^\d{8}$", v):
        return datetime.strptime(v, "%Y%m%d")
    return parse_iso_date(v)


def _ical_attach_event_page(value: str) -> str:
    """Return a human event-detail page derived from an iCal ATTACH URL.

    Some municipal IONAS feeds put the organizer homepage in ``URL`` but include
    an image attachment whose path lives under the real event detail page, e.g.
    ``.../2026-06-12-jazzig-in-die-ferne-swingen/poster.jpg?cid=...``. The image
    itself is a bad event link; its parent directory is the readable event page.
    """
    raw = _ical_unescape(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path or ""
    if "/kalender/" not in path:
        return ""
    if path.rstrip("/").split("/")[-1].lower().endswith((
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".ics",
    )):
        path = path.rsplit("/", 1)[0] + "/"
    elif not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _ical_feed_page(url: str) -> str:
    """Convert an iCal export URL to its human calendar page fallback."""
    parsed = urllib.parse.urlparse(url or "")
    path = parsed.path or ""
    if path.endswith("/event.ics"):
        path = path.rsplit("/", 1)[0] + "/"
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return url


def _ical_best_link(props: dict, feed_url: str) -> str:
    """Choose the most useful human URL for an iCal event."""
    attach_page = _ical_attach_event_page(props.get("ATTACH", ""))
    if attach_page:
        return attach_page
    return (props.get("URL", "") or _ical_feed_page(feed_url)).strip()


def fetch_ical(url: str, source: str, default_city: str, category: str = "", trust: float = 1.0) -> list:
    """Generic RFC 5545 iCal/.ics fetcher (Tribe Events, webcal, Meetup feeds)."""
    raw = _ical_unfold(fetch_url(
        url,
        timeout=20,
        accept="text/calendar,application/calendar+json;q=0.9,*/*;q=0.8",
        sec_fetch_mode="no-cors",
        sec_fetch_dest="empty",
    ))
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S):
        props = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, val = _ical_content_line(line)
            if not val:
                continue
            name = key.split(";")[0].strip().upper()
            if name in ("SUMMARY", "DTSTART", "DTEND", "DESCRIPTION", "LOCATION", "URL", "CATEGORIES", "ATTACH"):
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
            _ical_best_link(props, url),
            source, cat, trust,
        )
        if ev:
            events.append(ev)
    return events


# ── Web-search helper (shared by Exa + Grok) ────────────────────────

def search_result_event(title: str, link: str, desc: str, source: str, trust: float) -> Optional[dict]:
    """Convert a search result into a low-trust event, or None if out-of-window/radius/junk."""
    full_text = f"{title} {desc} {link}"
    extracted_dates = extract_dates(full_text)
    if not extracted_dates:
        return None
    if not date_range_overlaps(extracted_dates):
        return None
    city_guess = guess_city_from_text(full_text) or "Bonn area"
    km = haversine(BONN_LAT, BONN_LON, *coords_for_city(city_guess))
    if km > MAX_RADIUS_KM:
        return None
    candidate = {
        "title": unescape(clean_html(title)),
        "date": extracted_dates[0].strftime("%Y-%m-%d") if extracted_dates else "",
        "time": "",
        "venue": "",
        "city": city_guess.title(),
        "description": clean_html(desc),
        "price": "",
        "link": link,
        "distance_km": round(km, 1),
        "score": round(distance_score(km) * category_score(full_text) * trust, 2),
        "source": source,
        "category": "search fallback",
    }
    return None if is_junk_event(candidate) else candidate


def log_source_error(source: str, err: Exception) -> None:
    """Uniform stderr warning for a source that failed."""
    import sys
    print(f"⚠ {source}: {err}", file=sys.stderr)
