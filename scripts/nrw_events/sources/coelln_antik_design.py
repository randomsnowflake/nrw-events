"""Direct market dates from the Cölln Antik&Design organizer schedule.

This is a *different* operator from the already integrated Cölln Konzept, and it
runs different venues: the Kölner Flora, the Gürzenich, the Neumarkt, the
Maternusplatz in Rodenkirchen and the Rheinauhafen.

The page is hand-maintained WordPress content: a paragraph naming the market and its
address, followed by a list of dates. The date strings are correspondingly loose —
weekday prefixes including holiday names ("Ostersonntag"), several days joined by
``+`` or ``und``, the month named once at the end, and outright typos in the year
("27. Dezember 222026", "16. Mai 22027").

Implausible years are dropped with a logged warning rather than repaired. Guessing
that "222026" means 2026 would be right here and wrong elsewhere, and every affected
date sits far outside any normal reporting window anyway.
"""

import re
from datetime import datetime

from .. import common
from ..dates import MONTH_DE
from . import regional_common as rc


_URL = "https://coelln-antik-design.de/?page_id=13"
_SOURCE = "Cölln Antik&Design"

_ENTRY_CONTENT = re.compile(
    r'<div class="entry-content">(.*?)</div>\s*<!--\s*\.entry-content', re.S | re.I)
_PARAGRAPH_OR_LIST = re.compile(r"<(p|ul)\b[^>]*>(.*?)</\1>", re.S | re.I)
_LIST_ITEM = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)
_STRONG = re.compile(r"<strong\b[^>]*>(.*?)</strong>", re.S | re.I)

_MONTH_NAMES = "|".join(sorted(MONTH_DE, key=len, reverse=True))
_MONTH_IN_TEXT = re.compile(rf"\b({_MONTH_NAMES})\b\.?", re.I)
_DAY_IN_TEXT = re.compile(r"\b(\d{1,2})\.")
_YEAR_IN_TEXT = re.compile(r"\b(\d{4,})\b")
_POSTAL_CITY = re.compile(r"\b\d{5}\s+([A-Za-zÄÖÜäöüß][\w.\-]*(?:\s[A-ZÄÖÜ][\w.\-]*)?)")
_PRICE = re.compile(r"Eintritt\s*([\d.,]+\s*(?:EUR|€))", re.I)
_HOUR_RANGE = re.compile(r"\b(\d{1,2})\s*[–-]\s*(\d{1,2})\s*(?:Uhr)?")


def _plausible_year(value: int) -> bool:
    """Accept only years a hand-maintained schedule could legitimately name."""
    return common.TODAY.year - 1 <= value <= common.TODAY.year + 3


def _hour_range(text: str) -> str:
    """Render "11 – 18 Uhr" as a normalized time span.

    Scans every candidate rather than only the first: a street number range such as
    "Martinstraße 27-38" precedes the opening hours in these address lines, and
    stopping at the first match would drop the time.
    """
    explicit = rc.time_text(text)
    if explicit:
        return explicit
    for match in _HOUR_RANGE.finditer(text or ""):
        start, end = int(match.group(1)), int(match.group(2))
        if 0 <= start <= 23 and 0 <= end <= 23 and end > start:
            return f"{start:02d}:00–{end:02d}:00"
    return ""


def _parse_date_item(text: str):
    """Return (start, end) for a single list entry, or (None, None)."""
    year_match = _YEAR_IN_TEXT.search(text)
    if not year_match:
        return None, None
    raw_year = year_match.group(1)
    if len(raw_year) != 4 or not _plausible_year(int(raw_year)):
        common.log_source_error(
            _SOURCE,
            ValueError(f"implausible year {raw_year!r} in date entry {text.strip()!r}"),
        )
        return None, None
    year = int(raw_year)

    months = [MONTH_DE[m.group(1).casefold()]
              for m in _MONTH_IN_TEXT.finditer(text)
              if m.group(1).casefold() in MONTH_DE]
    days = [int(m.group(1)) for m in _DAY_IN_TEXT.finditer(text)]
    if not months or not days:
        return None, None
    try:
        start = datetime(year, months[0], days[0])
    except ValueError:
        return None, None
    if len(days) < 2:
        return start, None
    try:
        end = datetime(year, months[-1], days[-1])
    except ValueError:
        return start, None
    return (start, end) if end > start else (start, None)


def _market_heading(paragraph_html: str) -> tuple[str, str]:
    """Split the market name from its address/price line."""
    strong = _STRONG.search(paragraph_html)
    name = rc.clean(strong.group(1)) if strong else ""
    remainder = _STRONG.sub(" ", paragraph_html)
    detail = rc.clean(remainder)
    if not name:
        lines = [rc.clean(part) for part in re.split(r"<br\s*/?>", paragraph_html)]
        lines = [line for line in lines if line]
        if not lines:
            return "", ""
        name, detail = lines[0], " ".join(lines[1:])
    return name, detail


def events_from_page(html: str) -> list:
    """Parse paragraph/list pairs into events; unparseable entries are skipped."""
    content_match = _ENTRY_CONTENT.search(html or "")
    content = content_match.group(1) if content_match else (html or "")

    events = []
    pending_name = pending_detail = ""
    for tag, inner in ((m.group(1).lower(), m.group(2))
                       for m in _PARAGRAPH_OR_LIST.finditer(content)):
        if tag == "p":
            name, detail = _market_heading(inner)
            # A paragraph without a market name is prose between sections; keep the
            # previous heading rather than attaching its dates to nothing.
            if name:
                pending_name, pending_detail = name, detail
            continue
        if not pending_name:
            continue

        city = ""
        city_match = _POSTAL_CITY.search(pending_detail)
        if city_match:
            city = city_match.group(1).strip().split("-", 1)[0]
        if not city:
            continue
        resolved_coords, _, _ = common.resolve_location(city)
        if not resolved_coords:
            continue

        price_match = _PRICE.search(pending_detail)
        price = rc.clean(price_match.group(1)) if price_match else ""
        time_text = _hour_range(pending_detail)

        for item in _LIST_ITEM.finditer(inner):
            item_text = rc.clean(item.group(1))
            if not item_text:
                continue
            start, end = _parse_date_item(item_text)
            if start is None:
                continue
            description = common.factual_event_description(
                pending_name,
                date_value=start,
                time_text=time_text,
                venue=pending_detail,
                city=city,
            )
            event = common.make_event(
                pending_name,
                start,
                end,
                pending_detail,
                city,
                description,
                _URL,
                _SOURCE,
                "antikmarkt designmarkt kunstmarkt trödelmarkt markt",
                0.9,
                time_text,
            )
            if event:
                if price:
                    event["price"] = price
                events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    try:
        parsed = events_from_page(common.fetch_url(_URL, timeout=20))
    except Exception as exc:
        common.log_source_error(_SOURCE, exc)
        return []
    common._record_endpoint(
        _URL,
        parser_type="html",
        parsed_event_count=len(parsed),
        parser_empty=not bool(parsed),
    )
    return parsed
