"""Market dates from the marktcom directory, scoped by radius and format.

marktcom is the one market directory whose search is addressable by *both* a radius
around a coordinate and a market format, which lets the importer ask only for the
second-hand formats this project wants instead of filtering produce markets back out
afterwards. ``Wochenmarkt`` (31), ``Garten, Pflanzenmarkt`` (43) and
``Tiere und Zubehör`` (16) are simply never requested.

Two properties of the listing shape the parser:

* The ``eventname`` is the *venue* ("Hit-Markt", "ASV"), not the market name, so
  cross-source title matching against the directly integrated organizers cannot
  work. The listing does expose the organizer, so records belonging to an organizer
  we already read first hand are dropped instead — deduplicating at the source
  rather than hoping a fuzzy title comparison catches it.
* Results are ordered by date, so pagination stops at the first page that starts
  after the reporting window. A short window therefore costs one request per
  format.

Only listing pages are fetched; the per-event detail pages are never requested.
"""

import re

from .. import common
from ..models import AdmissionDefault
from . import regional_common as rc


_BASE_URL = "https://www.marktcom.de"
_SOURCE = "marktcom"
_SOURCE_ID = "marktcom"
_LISTING_PATH = "/termine/radius"

# Verified against the live category select plus the unlisted-but-live id 42.
# Deliberately excluded: 31 Wochenmarkt, 43 Garten/Pflanzenmarkt, 16 Tiere,
# 41 Weihnachtsmarkt, 44 Mittelaltermarkt, 3 Stadtfest, 37 Messen, 19 Ausland,
# 40 Stoffmarkt, 17/38/5 vehicle+computer markets, 12 Bambini (covered first hand
# by Kinderflohmarkt.com) and 4 Kunsthandwerk.
WANTED_CATEGORIES = {
    42: "Antik-Trödelmarkt",
    1: "Floh-, Trödel- & Jahrmarkt",
    2: "Antik- & Sammlermarkt",
    39: "privater Hof-/Garagentrödel",
    35: "Flohmarkt nur Privatanbieter",
    14: "Nachtmarkt",
    34: "Trödelhalle / Antikladen",
    47: "Second-Hand & Lifestylemarkt",
    7: "Musik-, CD- & Schallplattenbörse",
    8: "Film-, Comic- & Figurenbörse",
    13: "Briefmarken-, Münzen- & Ansichtskartenbörse",
    6: "Modellbau-, Eisenbahn- & Spielzeugmarkt",
    11: "Antiquariat & Bücherbörse",
}

# These structured marktcom formats describe visitor-free second-hand markets by
# nature. The listing often names only the venue (for example "Trödelfabrik"), so
# title-based admission inference cannot reliably see the market type. Explicit
# visitor charges still override this default in common.make_event; seller fees do
# not, because they are not admission.
_FREE_BY_NATURE_CATEGORIES = frozenset({1, 35, 39, 42})
_FREE_BY_NATURE_EXCLUSION_PATTERN = re.compile(
    r"\b(?:nachtflohmarkt|indoor[-\s]?(?:floh|trödel|troedel)?markt|messe|"
    r"stadthalle|eventhalle|ticket(?:s|preis)?|besucher(?:eintritt|preis))\b",
    re.IGNORECASE,
)

# Organizers already read first hand. A directory copy of their markets adds no
# coverage and cannot be title-matched against the first-party record, so it is
# dropped here rather than published as a near-duplicate.
#
# Keep this list in step with the registry: an entry for an organizer that is *not*
# registered as its own source silently drops coverage.
_INTEGRATED_ORGANIZERS = (
    "geide", "grote", "hiller", "lampert", "okken", "cölln", "coelln",
    "hofflohmärkte", "hofflohmaerkte", "hoffloh", "kinderflohmarkt",
    "rhein antik", "rhein-antik", "brückenforum", "brueckenforum",
    "katharinenhof", "melan", "krewelshof",
)

# Safety stop for pagination; reaching it is logged rather than silently truncated.
_MAX_PAGES = 12

_BLOCK_SPLIT = re.compile(r"(?=<li class='p-2'>)")
_EVENTNAME = re.compile(
    r"<div class='eventname[^']*'>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_POSTAL_CITY = re.compile(r"<div class='d-md-none'>\s*(\d{5})\s+([^<]+?)\s*</div>")
_ORGANIZER = re.compile(r"<p class='cat'>\s*(.*?)\s*</p>", re.S)
_DESCRIPTION = re.compile(r"<p class='description[^']*'>\s*(.*?)\s*</p>", re.S)
_DATE = re.compile(r"fa-calendar'></i>\s*(\d{2}\.\d{2}\.\d{4})")
_CATEGORY_ICON = re.compile(r"/system/icons/(\d+)/")
_NEXT_PAGE = re.compile(r"page=(\d+)")
_MARKET_NAME_HINT = re.compile(r"markt|börse|boerse|trödel|troedel|floh|basar", re.I)
_LISTING_CONTAINER = re.compile(
    r"class=['\"][^'\"]*\bmarktliste\b[^'\"]*['\"]",
    re.I,
)


def listing_url(category_id: int, page: int = 1) -> str:
    """Build the radius+format search URL for one page."""
    query = (
        f"lat={common.BONN_LAT}&lng={common.BONN_LON}"
        f"&radius={common.runtime_radius_km()}&stadt=Bonn"
        f"&q%5Bevent_kategorie_eq%5D={category_id}&q%5Bdatum_gteq%5D="
    )
    if page > 1:
        query += f"&page={page}"
    return f"{_BASE_URL}{_LISTING_PATH}?{query}"


def _is_integrated_organizer(organizer: str) -> bool:
    normalized = " ".join((organizer or "").casefold().split())
    return any(marker in normalized for marker in _INTEGRATED_ORGANIZERS)


def _market_title(event_name: str, category_label: str, city: str) -> str:
    """Build a title that is both readable and distinguishing.

    The ``eventname`` is sometimes the market name ("Antik- und Trödelmarkt") and
    sometimes only the venue ("Pferderennbahn Köln-Weidenpesch"). The description is
    not a usable fallback: its opening sentence is marketing prose
    ("Auf dem Antik- und Trödelmarkt ist der Name Programm").

    The venue is always kept in the title when it is not itself a market name,
    because a bare "<format> <city>" collides across the several distinct markets a
    city can host on one day, and identical titles would dedup real markets away.
    """
    name = rc.clean(event_name)
    if not name:
        return f"{category_label} {city}".strip()
    if not _MARKET_NAME_HINT.search(name):
        name = f"{category_label} {name}"
    if city and city.casefold() not in name.casefold():
        name = f"{name} {city}"
    return name.strip()


def _detail_title(html: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.S | re.I)
    return rc.clean(match.group(1)) if match else ""


def events_from_listing(html: str, category_id: int, detail_fetcher=None) -> list:
    """Parse one listing page. Ad blocks and integrated organizers are skipped."""
    query_category_label = WANTED_CATEGORIES.get(category_id, "Markt")
    events = []
    for block in _BLOCK_SPLIT.split(html or ""):
        name_match = _EVENTNAME.search(block)
        date_match = _DATE.search(block)
        city_match = _POSTAL_CITY.search(block)
        if not (name_match and date_match and city_match):
            continue
        # A record may be filed under a different format than the one requested;
        # trust the badge icon over the query parameter.
        icon_match = _CATEGORY_ICON.search(block)
        badge_category_id = int(icon_match.group(1)) if icon_match else category_id
        if badge_category_id not in WANTED_CATEGORIES:
            continue
        category_label = WANTED_CATEGORIES.get(
            badge_category_id,
            query_category_label,
        )

        organizer_match = _ORGANIZER.search(block)
        organizer = rc.clean(organizer_match.group(1)) if organizer_match else ""
        if _is_integrated_organizer(organizer):
            continue

        start = common.parse_date(date_match.group(1))
        city = rc.clean(city_match.group(2))
        # marktcom lists markets across Germany; never coerce an unknown postal
        # town into Bonn just because the gazetteer does not know it.
        resolved_coords, _, _ = common.resolve_location(city)
        # Some listings append a district after a spaced separator. Resolve the
        # full municipality first so hyphenated names such as
        # "Neunkirchen-Seelscheid" are not truncated to a different town.
        if not resolved_coords and " - " in city:
            city = city.split(" - ", 1)[0].strip()
            resolved_coords, _, _ = common.resolve_location(city)
        if not resolved_coords:
            continue

        link = rc.abs_url(_BASE_URL, name_match.group(1))
        venue = rc.clean(name_match.group(2))
        if detail_fetcher and re.search(r"(?:\.\.\.|…)$", venue):
            try:
                complete_title = _detail_title(detail_fetcher(link))
                if complete_title:
                    venue = complete_title
            except Exception as exc:
                common.log_source_error(f"{_SOURCE} detail", exc, source_id=_SOURCE_ID)
        description_match = _DESCRIPTION.search(block)
        raw_description = rc.clean(description_match.group(1)) if description_match else ""
        raw_description = re.sub(r"\s*\[mehr\]\s*$", "", raw_description).strip()
        title = _market_title(venue, category_label, city)
        description = raw_description or common.factual_event_description(
            title, date_value=start, venue=venue, city=city)
        default_free_admission = (
            badge_category_id in _FREE_BY_NATURE_CATEGORIES
            and not _FREE_BY_NATURE_EXCLUSION_PATTERN.search(
                " ".join((title, description, venue, organizer))
            )
        )
        event = common.make_event(
            title,
            start,
            None,
            venue,
            city,
            description,
            link,
            _SOURCE,
            f"{category_label} markt trödelmarkt flohmarkt",
            0.75,
            source_id=_SOURCE_ID,
            admission=(
                AdmissionDefault.FREE_BY_NATURE
                if default_free_admission
                else None
            ),
        )
        if event:
            if organizer:
                event["organizer"] = organizer
            # Keep source prose private until the common AI extraction pass.
            # The canonical boundary guarantees it can never be published.
            events.append(event)
    return events


def _page_starts_after_window(html: str) -> bool:
    """Results are date-ordered, so a page beyond the window ends pagination."""
    dates = [common.parse_date(value) for value in _DATE.findall(html or "")]
    dates = [value for value in dates if value]
    return bool(dates) and min(dates) > common.END_DATE


def _has_page(html: str, page: int) -> bool:
    return any(int(found) >= page for found in _NEXT_PAGE.findall(html or ""))


def _listing_contract_present(html: str) -> bool:
    """Return whether the known listing container exists, even with no events."""
    return bool(_LISTING_CONTAINER.search(html or ""))


def _fetch_category(category_id: int) -> list:
    events = []
    for page in range(1, _MAX_PAGES + 1):
        url = listing_url(category_id, page)
        html = common.fetch_url(url, timeout=25)
        parsed = events_from_listing(
            html,
            category_id,
            detail_fetcher=lambda detail_url: common.fetch_detail_url(
                detail_url,
                cache_namespace="marktcom-title",
                timeout=20,
            ),
        )
        common._record_endpoint(
            url,
            parser_type="html",
            parsed_event_count=len(parsed),
            parser_empty=not _listing_contract_present(html),
        )
        events.extend(parsed)
        if _page_starts_after_window(html) or not _has_page(html, page + 1):
            break
    else:
        common.log_source_error(
            _SOURCE,
            RuntimeError(
                f"category {category_id}: stopped at the {_MAX_PAGES}-page safety "
                "limit; later dates in this format were not read"
            ),
            source_id=_SOURCE_ID,
        )
    return events


def fetch() -> list:
    events = []
    for category_id in WANTED_CATEGORIES:
        try:
            events.extend(_fetch_category(category_id))
        except Exception as exc:
            common.log_source_error(
                f"{_SOURCE} (category {category_id})",
                exc,
                source_id=_SOURCE_ID,
            )
    return rc.dedupe(events)
