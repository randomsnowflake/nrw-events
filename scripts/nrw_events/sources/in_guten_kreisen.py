"""In guten Kreisen — first-party wine events in Bonn."""

from __future__ import annotations

from datetime import datetime, timedelta
import re

from .. import common


_ICAL_URL = "https://in-guten-kreisen.de/winedb/ical.php"
_WEEKEND_URL = "https://in-guten-kreisen.de/events/wein-ins-wochenende/"
_WEEKEND_DESCRIPTION = (
    "Am Freitagabend werden um 19 Uhr in gemütlicher Runde drei Weine "
    "vorgestellt. Danach können die Favoriten bis Ladenschluss weiter "
    "verkostet werden."
)


def _fridays() -> list[datetime]:
    cursor = common.TODAY
    while cursor.weekday() != 4:
        cursor += timedelta(days=1)
    dates = []
    while cursor <= common.END_DATE:
        dates.append(cursor.replace(hour=19, minute=0, second=0, microsecond=0))
        cursor += timedelta(days=7)
    return dates


def _official_weekend_price(document: str) -> str:
    """Build the explicitly advertised Friday series from its official page."""
    if not re.search(r"Ihr\s+könnt\s+euer\s+Ticket\s+an\s+einem\s+beliebigen\s+Freitagabend", document, re.I):
        raise ValueError("official Wein ins Wochenende recurrence marker missing")
    price_match = re.search(r"Tickets\s+bekommt\s+ihr\s+für\s+(\d+)\s*,-?\s*€", common.clean_html(document), re.I)
    if not price_match:
        raise ValueError("official Wein ins Wochenende price marker missing")
    return price_match.group(1)


def fetch() -> list:
    events = common.fetch_ical(
        _ICAL_URL,
        "In guten Kreisen",
        "Bonn",
        "Weinprobe Genuss",
        1.0,
        "in-guten-kreisen",
    )
    try:
        document = common.fetch_detail_url(
            _WEEKEND_URL,
            cache_namespace="in-guten-kreisen-series-v1",
            timeout=20,
        )
        price = _official_weekend_price(document)
    except Exception as exc:
        common.log_source_error("In guten Kreisen Friday series", exc)
        return events
    for start in _fridays():
        event = common.make_event(
            "Wein ins Wochenende",
            start,
            start.replace(hour=20),
            "In guten Kreisen",
            "Bonn",
            _WEEKEND_DESCRIPTION,
            _WEEKEND_URL,
            "In guten Kreisen",
            "Weinprobe Genuss",
            1.0,
            time_text="19:00–20:00",
            source_id="in-guten-kreisen",
        )
        if event:
            event["price"] = f"{price} €"
            event["admission_basis"] = "explicit"
            events.append(event)
    return events
