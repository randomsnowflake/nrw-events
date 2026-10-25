"""
Rheinaue flea market — Bonn's recurring open-air flea market.

Reads:  the organizer's (Melan macht Märkte, acting for the City of Bonn)
        location page, whose "Markttermine" box lists every published date.
Yields: one event per listed Saturday inside the window; nothing when the
        organizer publishes no dates (between seasons). Dates are never
        extrapolated from the usual third-Saturday rhythm.

The former bonn.de detail page (…/hauptkalender/flohmarkt-rheinaue.php) has
returned HTTP 404 since October 2026; bonn.de calendar entries for the market
still arrive through the general Bonn.de Events source.
"""

import re
from datetime import datetime

from . import regional_common as rc
from .fixed_markets import FixedMarketSpec, MarketOccurrence, events_from_occurrences, fetch_market

_URL = "https://www.melan.de/fuer-alle/standorte/details/~/Bonn-RHEINAUE-3l/"
_SPEC = FixedMarketSpec(
    title="Rheinauen-Flohmarkt",
    venue="Rheinaue",
    city="Bonn",
    source="Rheinauen-Flohmarkt",
    source_id="rheinauen-flohmarkt",
    url=_URL,
    trust=1.0,
    description=(
        "Trödelmarkt im Freizeitpark Rheinaue, auf dem nur gebrauchte Waren "
        "und Kunsthandwerk verkauft werden dürfen."
    ),
    category_hint="markt flohmarkt outdoor",
    timeout=20,
    empty_is_healthy=lambda html: _dates_box(html)[1],
)


def _dates_box(html: str) -> tuple[str, bool]:
    """Return the Markttermine box of the Rheinaue page and whether it says "no dates"."""
    html = html or ""
    identity = (
        re.search(r'class="market-name"[^>]*>\s*Bonn RHEINAUE\s*<', html)
        and re.search(r"Ludwig-Erhard-Allee 20\s*(?:<br\s*/?>)?\s*53175 Bonn", html)
    )
    box = re.search(r'id="market-dates"(.*?)</aside>', html, re.S)
    if not (identity and box):
        return "", False
    return box.group(1), "missing-market-dates-notice" in box.group(1)


def _events_from_page(html: str, *, strict: bool = False) -> list:
    box, no_dates = _dates_box(html)
    dates = re.findall(r"<span>\s*[A-Z][a-z], (\d{2})\.(\d{2})\.(20\d{2})\s*</span>", box)
    hours = re.search(
        r"<h3>Öffnungszeit</h3>\s*<p>\s*(\d{1,2})[.:](\d{2}) bis (\d{1,2})[.:](\d{2}) Uhr",
        html or "",
    )
    if no_dates and not dates:
        return []
    if not (box and dates and hours):
        if strict:
            raise rc.ParserEmptyError("Melan Rheinaue page, date box, or opening-hours contract changed")
        return []
    start_hour, start_minute, end_hour, end_minute = (int(value) for value in hours.groups())
    occurrences = []
    for day, month, year in dates:
        try:
            start = datetime(int(year), int(month), int(day), start_hour, start_minute)
            end = start.replace(hour=end_hour, minute=end_minute)
        except ValueError as exc:
            if strict:
                raise rc.ParserEmptyError("Melan Rheinaue date contract changed") from exc
            return []
        occurrences.append(MarketOccurrence(
            start, end, f"{start_hour:02d}:{start_minute:02d}–{end_hour:02d}:{end_minute:02d}",
        ))
    return events_from_occurrences(_SPEC, occurrences)


def fetch() -> list:
    return fetch_market(_SPEC, lambda html: _events_from_page(html, strict=True))
